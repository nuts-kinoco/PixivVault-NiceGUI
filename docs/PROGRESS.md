# 移植進捗

## 2026-10-23 Phase8 / CoSCore後任・GitHub現物照合と現在の作業

**Phase7統合は完了**。PR #28 head `9c906b5630bc8fecc83fd2c4462adcd247fd6bbb` のCI run **39956405545**はWindows/Ubuntu/browserの3ジョブSUCCESS。2026-10-23 12:59:14 UTCマージ、main merge SHA **`2ae2b49d389c4cd0da398a8cd10f8621dc1cc669`**。post-merge CI run **39957517383**もWindows/Ubuntu/browserすべてSUCCESS。前節のPhase7未マージ/Windows REDは過去の記録（修正前CI run 39955280876）であり現在状態ではない。

**Phase8 worktree**：`S:\Projects\PixivVault-NiceGUI-wt\legacy-diff-sync`（branch `phase8/legacy-diff-sync`、開始基準`2ae2b49d389c4cd0da398a8cd10f8621dc1cc669`）。`main`/他worktreeへの直接編集なし。原版`S:\Projects\PixivVault`のコードだけ読取専用照合。新管理rootのみに変更を限定し、静止旧DBコピー・旧画像/ZIPはread-only。詳細契約 `docs/LEGACY_AUTHOR_DIFF_SYNC_2026-10-23.md`。

**導入中の実操作**：旧SQLiteコピーのworks/failed_queueと旧物理ZIP/フォルダを既存`ReadonlyLocalMediaCatalog`で突き合わせ、作者のNEW/UPDATED/RESTORED/MISSING/UNKNOWN/SKIPPEDを差分一覧に表示。実体確認した旧作品の復帰・欠損を**新managed保存root**へ明示キュー投入する（旧ZIP追記・旧DB書換えは行わない）。管理receiptの完全検証と既存v2 CAS、JobServiceを再利用。旧DBに載るが現在の作者一覧に無い作品は、明示「旧DBと照合して欠落を確認」を押した完全GETだけで欠落回数を新sidecar SQLiteに記録（通常3回、高比率5回）。旧削除フラグは新管理receiptの検証まで復帰扱いを維持する。最大2000差分行を表示、上限超は結果に表示件数を明記。

**段階検証**：専用CLI/service/UIの関連pytest 33 passed（初回）、統合拡張後21 passed、workerのlocal media/full GET 144 passed。初回全pytest **1157 passed / 1 skipped / 1 failed**：`test_changed_remote_is_atomic_and_two_clients_cannot_double_count`でsidecar SQLite新規作成中の0-byteに別clientが遭遇（並行初期化競合）。原因を隠さずworkerに修正指示済み。同時点のUI/service/bridge関連53 passed。**全pytest・独立レビュー・Edge2解像度・nativeコピーEXE・head/main CIは現在まだPhase8未受入**。再試験後の確定結果をここへ追記する。

**RED→GREENの続報（23:08 JSTまでの限定結果）**：sidecar初期化を並列化した16client合成試験GREEN。同じ専用venvと明示`PYTHONPATH`で全pytest **1172 passed / 1 skipped / 1 Starlette依存deprecated警告**（61.52秒）に回復。作業途中で4件出たsubprocess import/CLI失敗は別worktreeを指すeditableパスが優先されたためで、`PYTHONPATH=当Phase/src`を子プロセスへ継承して全件GREENを確認。追加差分により最終再試験は別に必要。

**最終再試験（23:59 JST時点）**：空`[]`bucketをstrict/通常reader双方で明示0件として受け入れるテストを更新後、全pytest **1179 passed / 1 skipped / 1 Starlette deprecation warning**（60.08秒）、exit 0。対象作者の旧worksだけstrict validateし、別作者のNULL/古いスキーマ行で全体を落とさない修正は38専用結合テストGREEN。Claude focused read-only re-reviewはP1/P2なしでAPPROVE（テストを実行したという意味ではなく静的承認）。

**Phase8専用Edge checkerのRED→GREEN**：原因はブラウザランナーに渡す合成DB pathが相対パスだったこと。`ApplicationServices` は旧DBコピーの絶対パスを要求するため `ValueError: legacy retry requires a separate immutable SQLite copy`。検査器で`output.resolve()`を先に行い、DB・メディアroot・managed rootを絶対パスに統一して修正。次に初期タブがDownloadではなく、保存済み作品の再起動時は選択数0になるなど、UIの実挙動に合わせて検査器を修正した。失敗出力は`artifacts/phase8-legacy-author-diff-edge-final3-20261023/`〜`final8`に保持。

最終 `tools/check_legacy_author_diff_browser.py --channel msedge --output artifacts/phase8-legacy-author-diff-edge-final19-20261023` は**SUCCESS**（`result.json`）。1280×720/1920×1080 ×初回/再起動4/4。初回は実際のDOMからUPDATED/RESTORED/MISSING/UNKNOWNを確認し、管理rootへの3作品保存を受け付け、再起動後はSKIPPED/UNKNOWNとなり完了3件を復元。作者差分表示は二つのブラウザclient間で分離。GET-only・API HTTPS/allowlisted host・Cookie付きAPI GET・Cookieなし画像GETを全transport経路で検証。旧DBと旧メディアルート全ファイルSHA-256 before/after一致、再起動時原寸GET 0、console/page error0、外部通信0、通常終了4/4・ポート解放4/4。新sidecarは管理rootだけに存在し、旧DB/sidecarの`-wal/-shm/-journal`はいずれも不在。再起動前後の欠落streakは3、削除確定=true、巡回receipt1件で変化なし。差分表示ms `143.34,126.95,161.12,119.55`（この合成fixture・開発機のみ）。元DB SHA `290cb9fd48e2c66478cf710f4d525f0b517c39caba09d74ddfaa7f69f0509881`、旧画像SHA `ba676f61c0fdc78461b1738c27bfdc3b5accb5fbbc6a1451b66f5a60a7d24032`。

**1280×720での受入RED→GREEN**：4状態を表示するとキュー追加ボタンが親のoverflow領域でヒットテストできず、Edgeがclick timeout（`submit_preflight`でbutton rectが画面下端、`pv-download-body.clientHeight=0`）になった。作者差分/遠隔欠落行へ`pv-author-diff-list`（max-height64px）と`pv-author-remote-absent-list`（max-height48px）の独立スクロール制限を追加し、実ブラウザでスクロール後に3件をキューへ追加、再起動後完了3件/状態を確認。最終Edge checker final19でGREEN。

**readerの通常空bucket**：review P3を解消。`pixiv_read_client.py`は明示空`[]`だけ`{}`へ正規化し、strict readerの非空配列/null/missing拒否を維持。`tests/test_author_pixiv_reader.py`の従来「空配列はInvalidPayload」期待を仕様に沿って更新し、全pytest GREEN。

**最終ソースWindows製品受入（Phase8、CSS修正後）**：`tools/build_windows.ps1 -Python 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'` 成功。PyInstaller onedir/noconsoleを`artifacts/windows-packaging/dist/PixivVault-NiceGUI/`に作成、そこから新しい`artifacts/phase8-native-final-copy2/`へコピーし1050ファイルの相対path+SHA-256を完全照合（`COPY_SHA_GREEN files=1050`）。コピーEXE SHA-256 `162E5F495320CC52F1C225627C1E75BE7E43F19EAC2A6573C02AE0562B3BF537`。`tools/check_native_window.py --exe artifacts/phase8-native-final-copy2/PixivVault-NiceGUI.exe --inspect-ui --output artifacts/phase8-native-final-result2.json` は`status=passed`：UI handshake/cards50/image decode、viewer/learning/library操作、最小化/復帰GDI安定（mean diff `0.0000172`）、通常終了、所有8 PID終了、port解放、強制cleanup0、console/page error0。開発機DPI 175%の実機条件であり、他PC/全DPIを証明しない。

**CI追加**：`.github/workflows/ci.yml` browser jobにPhase8専用検査器を追加し、Chromium CIでUPDATED/RESTORED/MISSING/UNKNOWN/SKIPPED・remote absence・元ソース不変性・sidecarの隔離・streak再起動維持・transport GET-onlyを実行する。これを含むheadのGitHub CIは**未実行**。CSS修正後の全pytestは`1179 passed, 1 skipped, 1 Starlette deprecation warning in 61.90s`。最新CSS/検査器の独立read-only re-reviewを待つ。

**Edge・Windowsの既存機能回帰（暫定ソース）**：`tools/check_author_download_browser.py --channel msedge`を1280×720・1920×1080 ×初回/同一root再起動で4/4 PASS（`artifacts/phase8-author-regression-20261023/result.json`）。初回の合成原寸GET4枚・再起動0、ビューアdecode/キーボード/フォーカス、2client分離、console/page error0、通常終了・port解放4/4、外部ブラウザ通信0。Windows PyInstaller onedir/noconsoleビルドは1050ファイル生成、別`artifacts/phase8-native-precheck/`へコピーし全1050ファイルSHA一致。コピーexe SHA-256 `628783AB6A716B477E2AFD411D91FC04408157F89AAC5380D0505AA80579C014`、`tools/check_native_window.py --inspect-ui` は合成UI/デコード、最小化/復帰、通常終了、8所有プロセス終了とport解放PASS（`artifacts/phase8-native-precheck-result.json`）。このEXEは最新の独立レビュー修正前にビルドしたため**Phase8最終ソースのnative受入済みとは数えない**。

**合成性能実測（限定条件）**：`artifacts/phase8-synthetic-metrics-v2/result.json`。1000作品・画像実体0・旧SQLiteコピー86,016 bytes・read-only完全作者プレビュー12回、中央値24.412ms・p95 29.765ms・最大34.858ms、計測プロセスRSS 26.188→peak28.344MiB、画像decode0/外部GET0。Windows合成CPU/IO/ページ数条件だけの値で、一般の巨大ZIP/実Cookie/高解像度描画の速度やメモリには外挿しない。

**独立Claude CLI read-onlyレビュー**：初回はP1なし/P2 3件（他作者の古いworks NULL行で作者照合が落ちる／profile/allの空bucket[]・masked等／途中タブ切替後のscan_id失効）。対象作者だけstrict validate・明示connection close、完全GETで明示空`[]`を0件扱い、通常readerにも空`[]`受入、部分一覧はmanaged-only/旧由来UNKNOWNへfallback、scan完了・replay-digest時にIDローテーションを実装。focused read-only re-reviewは**APPROVE、P1/P2なし**。CIはまだ未実行。

**未完境界**：実Cookieによる限定GET/作者差分DLは未受入。原版DB・ZIPへの書戻し、うごイラ/小説、scheduler、重複隔離・おすすめ/設定/連携、全画面/全DPIの製品置換も未受入。原版の`both`部分一覧不可視で削除検知する既知経路は新側でrejectする。性能/メモリは最終ソースで測った条件以外推測しない。

## 2026-10-23 Phase7統合前の引き継ぎ記録（履歴・後任Phase8より前）

**引き継ぎの入口**：`docs/ORCHESTRATOR_START_PROMPT.md`（後任へ全文渡す）と`docs/ORCHESTRATOR_HANDOFF.md`（統合履歴・残件・保全・受入SOP）。CoSCoreだけで開発し、dot（どっと）のユーザー側からの依頼にも安全な優先順位で対応する。両文書の製品head SHAは**作成前のスナップショット**であり、docs-only commit/push後には最新headとCIを再照合する。

**main**：PR #26（作者ID・NEW/UPDATED差分/世代CAS）統合後`05115e7f34bbe7e050bc0b563d383fd3117c2854`、PR #27（旧failed_queue静的コピー→管理ジョブ再投入）統合後`50ea5ee9ff745226cef6e7ac3fbc2a311c527d4f`。mainのCI run `39946846948`（head `50ea5ee9`）はWindows/Ubuntu/Chromium**全3成功**。他worktreeの未コミット変更は削除・上書きしていない。

**現在の次単位**：`phase7/legacy-zip-export` 専用worktree（`S:\Projects\PixivVault-NiceGUI-wt\legacy-zip-export`）。製品実装commit `a667a1e3b473c63b4d377b4e1acc793b8299282e`はpush済み。アクティブ管理原寸画像→専用新rootの原版互換作者ZIPを明示ボタンで作成。旧画像/DB/ZIP原本無変更、同作者ZIP・既存ZIP非上書き。v1/v2世代検証、故障時一時ZIP隔離、公開直後クラッシュ復元、shutdown途中中断、更新世代競合、複数client/古いepochを検証。原版ZIPへの追記・既存ZIP改名、元画像消去、全形式対応は未移植であり「完全互換」と誤称しない。

**ローカル最終受入（製品ソース時点）**：全pytest **1099 passed/1 symlink特権skip**、Starlette依存警告1、独立Claude CLI **APPROVE（P1/P2阻害0）**、実Edgeの1280×720/1920×1080×初回/再起動4経路SUCCESS（ZIP全ページ/画像decode/命名/CRC/非上書き/GETと端末終了/port）、Windows PyInstaller onedir/noconsole実ビルド→別rootへ**1050ファイルSHA一致**、コピーexe（SHA-256 `F1F629D175F384F9A11FE7051BEE0C02D8996560BABCED999ACC8E033BB6A5F6`）のUI/最小化/復帰/通常終了/ポート解放PASS。主な証拠はgit追跡外の `artifacts/legacy-zip-20261023/`。実CookieによるPhase7の実画像出力は未実行。最大1.7GiBの作者ZIPのp95/メモリ測定値は無い。

**PR #28と初回RED**：文書専用commit `ebcfc12c004ca42ab78354b59d8a5540db9ca651` push後、PR #28（`https://github.com/nuts-kinoco/PixivVault-NiceGUI/pull/28`）を作成。CI run `39955280876`ではUbuntu成功、Windows `tests/test_native_managed_downloads.py::test_actual_synthetic_writer_jobservice_verifier_detects_mismatch`が**5秒spin-poll時点で`running`のためRED**（1 failed/1099 passed）、Chromiumは未実行。製品書込と全ページ検証はそのままに、JobServiceの条件変数通知で実際のdurable terminal完了を有限20秒待機・done/total2・receipt path・faultedを厳格照合する修正を追加。読取専用レビュー**APPROVE**。これは**新headのCIがまだGREENになる前の修正**なので、成功と誤記しない。Windows runnerの遅延/負荷は可能性であり、根本原因を確証済みとは主張しない。

**次のゲート**：上記のCI修正と記録を対象パスだけcommit/push→PR #28の新head SHAに対するWindows/Ubuntu/ChromiumすべてSUCCESSを確認。旧レビュー・Edge/nativeの製品ソース一致を確認してmerge→新main CI検証。その後、未移植の原版差分のRESTORED/MISSING、scheduler、うごイラ/小説、旧保存名の詳細互換、重複整理、おすすめ/設定/外部連携、全面Windows製品受入へ続ける。各Phaseが最終ゴールではない。

## 2026-10-23 作者単位差分ダウンロード（PR統合前・受入継続中）

PR #25統合後のmain `bacb629ccfc36969b3e560b9d4596efd760c7fc8` を元に、別worktree `phase5/author-differential-downloads` で作者ID/フォロー選択からの差分取得を実装。原版の作者ID数値指定と24件フォロー・48件作者作品詳細GETを維持し、検索後の選択保持、由来、重複防止、解除、2client独立、非同期epoch/revisionを接続。作者NEWと既知更新日のUPDATEDを明示キュー投入し、v2 accepted-intent/旧v1読込/旧SQLite job互換、旧receipt全頁検証・不変世代保存・SHA+path CASのactive切替、旧画像・履歴を保存。v1更新日が無い作品は`UNKNOWN`とし自動再取得しない。

独立レビューで `/ajax/illust/{id}` に`updateDate`が無い実API境界を発見し、原版同様`/ajax/user/{uid}/profile/illusts`再GETへ修正。空`updateDate`もv2作者由来としてNEW保存できるが、以後は`UNKNOWN`（更新済みを捏造しない）。古い作者意図の手動再選択への混入と、共有bookmarkキャッシュの古い作品メタデータの再利用を修正。旧世代SHAだけのCAS（同一バイトの別世代）をREDで発見し相対pathも照合。全pytest **1051 passed / 1 Windows symlink権限skip**、1 Starlette依存警告（未緩和）、`artifacts/author-downloads-20261023/full-remote-contract-fixed.xml`。

実Edgeの合成初回/再起動×2解像度（1280×720/1920×1080）は、新規2頁＋更新2頁の原寸GET計4・再起動時0、active v2と旧世代残置、共有viewer画像decode・キーボード/フォーカス、通常終了/port解放4/4、browser例外0で成功。最終コードSHAに対する再受入を分ける。実Cookie作者経路とWindows新UI配布版での個別受入は未完であり、ツール側の機密データアクセス安全チェックにより実Cookieコピー試行は拒否された（Cookie原本不変）。元DB・画像・設定・Cookieを変更せず、公開証跡に実ID・認証値・画像を含まない。フェーズ5全体も全面移植も未完。詳細は `AUTHOR_DIFFERENTIAL_DOWNLOAD_2026-10-23.md`。

更新: 2026-10-23、レビュー後のマージ承認に基づく継続開発。最終ゴールは移植完了、各フェーズは途中のマイルストーン。現在の検証単位: `phase5/managed-pixiv-downloads`（原寸画像の管理保存・永続キュー・受理入力復元・保存済み閲覧）。

## 最新の進捗照合とフェーズレビュー

PR #24（main `640b72ccd892c6ef1a8a1252ad4e33d913e18265`）はhead/main CIの全3成功を照合済み。後続の独立`phase5/direct-work-download`では、ブックマーク外の数字作品IDをGETで確認してから明示キュー投入し、管理原寸保存/再起動後のライブラリ閲覧へ接続した。合成全975pytest/1権限skip、実Edge2寸法×初回・再起動の4経路で原寸2頁・未照会別client非表示・GET/port/画素を受入。独立レビューで判明した認証再検証中の競合、client間の手動ID許可漏れ、古いクリック/解除、送信受付中の終了競合をRED再現し修正した。Windowsコピーexe・新head/main CIは別ゲート。`DIRECT_WORK_DOWNLOAD_REVIEW_2026-10-23.md`参照。フェーズ5全体未完。

PR #24の初回head `a0d43467726f65281c64588437a9eff00565719c` のCI run39883545759で、任意依存のimport順序とLinux用テスト属性の注入に失敗を確認した。製品srcは変えず2件のREDを再現して検証器/テストを修正し、独立レビューPASS、`full-ci-compat.xml`で946 passed / 1 skippedを確認。既に受入した56製品入力と同一コピーexeは不変。修正headのCIとmerge後mainのCIは別途確定する。

PR #23のmain `a7b021a3001660ba38776ae3509426e0c322985a` とCI run39876795186全3成功を再確認し、新しい管理保存単位へ進んだ。通常イラスト/漫画のoriginal取得、全頁検証・専用管理保存、受理済み入力の永続化、再起動後error retry、保存済みライブラリ閲覧を接続。全945pytest成功/1symlink権限skip、実Edge2寸法×初回/再起動で原寸画素・再取得なし・2client保持を確認した。同一コピーexeで実Cookieによる1作品3頁の保存・ページ送り・戻り、同管理先再起動・保存物不変、合成復帰後のGDIと通常終了・port解放・Cookie原本不変を受入。独立レビューとClaudeの限定第二意見を利用し、確定境界・原本保護を検証した。先行FAIL試行と検証器修正を残し、head/main CIは別ゲート。詳細は`MANAGED_PIXIV_DOWNLOAD_REVIEW_2026-10-23.md`。旧DB/ZIP更新・作者巡回・うごイラ/小説・重複整理・設定等は未完。

失敗リストPR #22は最終head `3288bf0b2e72dea9e06575d10cfb7f721a714bf9` の全3CI成功後、main `1bcfdbb60b869a145bbc3c300ee837b485d80079` へ統合。main run `39873726737` もWindows/Ubuntu/Chromium全成功。旧mainの依存取得時間切れとは別の正確なSHAで確認した。

後続の合成保存・永続キューは全855pytest、実Edge2寸法で個別/一括pause-resume-stop、再試行、確認削除、12件のページ越え選択、13件の履歴削除と11receipt保持を確認。独立レビューで見つかったpaused待機による詰まり、DB保存待ちによるUI停止、削除確認対象・古いイベントの混入をRED再現して修正した。同一コピーexeの初回保存と再起動で4頁の全画素/SHA、履歴保持/削除とファイル保持、GDI復帰と各8process通常終了を受入。実pixivダウンロード・旧scheduler/failed_queue同期は未接続。詳細は `DOWNLOAD_JOBS_REVIEW_2026-10-23.md`。

PR #21はhead `3c7bb387792b69aafedda154e892150db02c99ea` からmain `5fee5ec91b06b6d9a56d40c75db398799dd10f31` へ統合済み。実Cookieの専用Edge受入 `real-ui-03` は2ページdecode、I/Tab、検索・選択・focus・タブ往復、原本不変、書込0、通常終了を確認。main CI run39828938139は依存取得で時間を消費してcancelledであり、greenとしない。

後続の失敗リスト単位は原版7列とカテゴリ・8件ページングを維持し、非同期読取・失敗時の前回状態保持・旧イベント拒否・絞込全件CSVを接続。全740pytest、失敗リスト実Edge2寸法と既存4系統が成功。同一コピーexeで所有SaveFileDialogから絞込3行を保存し、7列全フィールドと式無害化、GDI復帰、通常10process終了とport解放を確認。CSV未保存の検証偽陽性をRED再現して必須ゲート化し、独立再レビュー済み。詳細は `FAILED_RECORDS_PORT_REVIEW_2026-10-23.md`。再試行/削除・実ジョブ接続はdisabledのままで、段階5全体は未完。

PR #20はhead `69ce42c6651605bf35fcb62c5dbfae24957529c5` のCI全成功後にmain `e8835c4e4209efe3eb76c296889c672b0e6a3898` へmerge。継続時にmain CI run39784532379も全成功を再照合した。

後続の認証互換は旧metaとNext.js本人情報を厳格に検証し、表示不可masked作品が一覧全体を失敗させる問題を分離した。製品時点677pytest、検証器の偽陽性・終了要求回帰を加え681pytest、実reader+合成transportの新旧HTML各2解像度Edge、同一コピーexeの合成旧DB経路・GDI復帰・通常終了が成功。実Cookieの専用Edgeでも検索/選択/サムネイル/2ページdecode/I・Tab/戻りfocus/タブ往復を確認した（GET102、うち画像52、書込0、Cookie原本不変、実データ証跡保存なし、通常終了・port解放）。過去2試行のAssertionErrorと合成終了待ち失敗は保持し、一般的な根因解消を主張しない。詳細は `PIXIV_AUTH_COMPAT_REVIEW_2026-10-23.md`。このbranchのhead/main CIは別ゲート。段階5のjobs-pipeline/queue-stateは別のdirty worktreeで並行しており、上書き・重複統合しない。

タグ条件PR #19は並行PR #18を取り込んだhead `8233586ff1982ef0726bb7a7aec787532c8f12d5` で全581pytest、CI run39779893130全3ジョブ成功後、`13ec540c2d3364c7cd0bcf4d9ad1d1c73c784bd8` へmerge。main CI run39780880773も全成功。実データ書込みやフェーズ全体の完了へ拡大しない。

後続ではWindows native初期配置を製品側で修正。プライマリ作業領域とDPIから論理座標を内向きに計算し、既存窓を移動せず初回生成へ反映する。対象70/全635pytestと独立レビュー、既存Edge4系統/旧DB/合成transportが成功。コピーexe `b49de6ff...` の2起動とも、配置補助なしで従来のclient3336×1826物理pxを維持し、初期origin251,144、DPI168、UI/GDI復帰/通常8プロセス終了/port解放が成功。製品43入力、配布1050ファイル、合成fixture3ファイルのSHA一致を確認。詳細は `NATIVE_STARTUP_LAYOUT_REVIEW_2026-10-22.md`。任意モニター・全DPI実機・別PCは未完。

旧SQLite/保存画像/マイブックマーク読取統合はPR #17へcommit `97efc035ca5096b8c135d69f86d49c93435ae5cd`としてpush。ブラウザCIの依存取得がAzure Ubuntuミラーで時間切れになったため、テストや閾値は変えず公式Ubuntuミラーへ切り替えるCI修正を独立レビューし、head `e57d83d2ab608f7902a78701f860f1150fc076ba` のCI run39776394742全3ジョブ成功後にmergeした。main `70922d9add8d0f1fee7affe69560209540c16b29` のCI run39777248515もWindows/Ubuntu/Chromium全成功。

次に原版のタグ含む/除外・AND/OR、280px押し出しパネル、NOWチップ解除を実装。作者代表と詳細全ページは絞らず、条件はDL済み/ブックマーク間のみ共有してclient間を分離。独立レビュー指摘の固定ボタン遅延イベントをブラウザ世代照合で修正した。製品凍結531pytest、検証器回帰を加えた最終545pytest、実Edgeの既存4系統・旧DB・合成transport取得失敗/復旧/2client試験が成功。同一コピーexeでタグ操作と最小化復帰・正常8process終了まで合格したが、初期画面外配置を避ける明示検証条件付きであり、自動初期配置の修正はWindows残件。詳細は`LIBRARY_TAG_FILTER_REVIEW_2026-10-22.md`。この変更のPR/head/main CIは別ゲート。

PR #14後のPR #15/#16統合とmain `48c718fe6f8702606496f44e5ed7a9d24cd72050` のCI成功を確認。旧DB/保存画像・マイブックマークUIを独立worktreeへ統合し、未DL作品viewer、検索/選択/戻り、4経路失敗復旧、旧cache破損隔離、novel/illust同番号分離、2clientの画像snapshot保持を実装した。製品コード凍結時516pytest、既存4経路・旧DB接続・合成APIを通す実Edge 1280/1920が成功。配布1050ファイルを別フォルダーへコピーしSHA一致を確認。同一コピーexeで旧SQLite・保存PNG・永続ブクマ検索/選択/viewer、最小化/復帰GDI、通常8プロセス終了とport解放が成功（175%開発機限定）。実Cookie原本の読取はツール安全性チェックに阻まれ未実行のため、ライブ認証/実API受入とは区別する。実装・独立レビュー・失敗試行・最終証拠は`READONLY_BOOKMARK_INTEGRATION_2026-10-22.md`。PR head/main CIは別ゲート。フェーズ4全体と更新系は未完。

PR #9で初期ライブラリ作者経路がmainへ先行統合されたため、追加修正のみを別branchへ整理した。作者51件以降の明示追加、詳細中の古い別作者クリック拒否、再訪できなくなる回帰修正、作者カード閲覧数表示、欠落したnative UI guardと検索selectorを補正。独立レビューの再現→修正後は全体358pytest成功。実Edge4系統・ソース前後hash一致・port解放を確認。実装同一SHA bea9cbdのWindows onedir/noconsoleを別フォルダーから実走し、library導線、最小化復帰のGDI画素、通常8プロセス終了とport解放成功。175%開発機だけの受入で、原版未移植範囲は引き続き未完。詳しくは`LIBRARY_AUTHOR_VIEW_REVIEW_2026-10-22.md`。

次の段階4の限定単位として、合成ライブラリの作者4件集約→作者01の60作品98ページ→共有viewer→戻るを移植した。検索前代表固定、検索は作者一覧のみ、50ページ追加とDOM/scroll保持、0/1始まり変換、未知ページ数、保存日/閲覧数ソート、カードサイズ同期・client分離・旧イベント排除を確認。別worktreeで統合353pytestが成功。実Edgeの1920×1080/1280×720と2 client、画像decode・合成503 retryも成功し、実ブラウザ4系統のランナーも全0で完走（同条件の証拠に限定）。この単位の記録は `LIBRARY_AUTHOR_VIEW_REVIEW_2026-10-22.md`。PR headのCI確認と統合は別ゲートで、段階4全体の合格ではない。

学習保存PR #6は、実装`1011127f4e86c2c3a34583b1aae2836b04655c21`の試験・配布受入と、文書だけを追加した最終head`f35bd54908f6504230550c4373e19a3676eb0734`のCI run39717369777全3ジョブ成功後、`bf7d00637cfbd3257c2c31b2c476cc7e17a17735`としてmainへmergeした。main CI run39717642107もWindows/Ubuntu/Chromiumの全3ジョブ成功。

主画像比較PR #7は、独立レビューと最終head`c9ecb85198f158c3b51a96fe3976cbaac2988581`のCI run39722305638全3ジョブ成功後、`1261d020be52b76007e3f0da8a6c4f769fad98b5`としてmainへmerge。main CI run39724415694もWindows/Ubuntu/Chromiumの全3ジョブ成功。

続くコード`76ddc0fc2cadf36b635a4c3e02f649fe197c10a6`で、native最小化/復帰後の子geometry・DPI・全client実GDI画像を必須検査にした。初回に一時reparentを検出し、同identityが約51ms後の観測で元に戻ることを別診断で確認。所有権を維持した期限内再検査と標本破棄を追加した。対象112pytest、全体329pytestが成功。製品コードが同一の1011127配布exeへ標準checkerを適用し、学習UI操作あり/通常一覧の2回で実画素・通常8プロセス終了・ポート解放が成功した。primeと独立担当が4全画面を確認し、最終レビューに重大残件なし。新buildや製品復帰処理の変更ではなく、175% DPIでの追加受入。詳しくは`NATIVE_RESTORE_REVIEW_2026-10-22.md`。

先行の主画像比較では、旧Flet隔離launcherへ任意のlogical Page paddingを追加し、原UI7ファイルを保ったまま主画像の実描画矩形を一致させた。コード`6f2b66e53b275f0c614211e05d5f864e55c2f412`は独立レビュー済み、対象pytest22件成功。1920×1080物理client・175% DPI・同一PNG・868×579物理pxの画像で、Flet→NiceGUI→NiceGUI→Fletの30遷移ずつ、計120遷移が成功。60標本ずつの観測p95は250.330 / 122.386 ms（取得・照合負荷込み、51.1%短縮）。private bytes合計は408.199 / 539.828 MiB（32.2%増）であり、メモリ削減は確認できない。全画面校正、raw標本再計算、独立レビュー、通常終了と元source不変を確認した。詳細は`MATCHED_VIEWER_BENCHMARK_2026-10-22.md`。

開始前の最小化拒否/一様frame拒否2件は成功標本と別保存した。NiceGUIは外部ShowWindow復帰後に子window座標不正が残り、標準の最小化/復帰1cycleで実画素まで復旧した。この先行事象の原因は未確定。後続のnative gate強化は上記のとおり実施し、別試行の短いreparentと画面外配置の恒常化を同じ原因とは断定しない。今回の限定比較は原shell全体・cold・連打・配布exe・全DPIの性能受入ではない。

PR #5のhead `d5ba18a246da27c14071793413dbb45fe8a40e62` はレビュー/CI/測定器受入後、`bf1b04bb10881ac65d515376e230680a839f36a3` としてmainへmerge済み。head CI run39708638252、main CI run39709086223のWindows/Ubuntu/Chromium全3ジョブ成功を再確認して次の作業を開始した。

合成デモの学習UIを、明示opt-inの専用DBへ接続した。原版の直前1手Undoへ合わせ、保存成功まで画像/判定位置を保つ。全DB I/Oは専用workerで行い、画面close後も受理済み書込みを保存する。レビューでは受付直後shutdownの保存欠落、保存状態の読取失敗後に遅い画像通知が復旧案内を消す問題、DB本体の差替えを識別前に開く問題を修正した。既定はメモリ台帳、本番データへの接続はない。

全pytest266件、実Edgeの永続化10受入が成功。実DBロック中の別タブ検索、失敗再試行、再起動での既判定除外、2画面の古いUndo拒否、ページclose後の保存まで確認。元Database115メソッドのAST一致と元ソースSHAも維持。独立再レビューにblocking指摘なし。実装 `1011127f4e86c2c3a34583b1aae2836b04655c21` をPR #6へpushし、同SHA CI run39716131704の全3ジョブ成功。詳しい証拠は`LEARNING_PERSISTENCE_REVIEW_2026-10-22.md`。

同SHAのarchive266件が成功し、Windows onedir/noconsoleを再ビルド。別フォルダーのexeを同じ専用DBで2回起動し、既判定0→1→正常終了→再起動1→2と、reload後の次作品/Undoリセットを確認した。両回ともWM_CLOSEによる観測8プロセス終了、ポート解放、強制cleanupなし、予期しない各種error 0。DPI175%の開発機受入であり、他DPI/別PCは未実施。

ユーザーの追加要望による主画像の定量比較は、上記の同じ物理client/DPI・実画像paint矩形のwarmページ送りまで進んだ。先行の表示面積差がある数値は今回の改善率へ混ぜず、初回表示/coldは次の条件として残す。
さらに既存Cookieを用いるpixiv実APIの読取・実測が承認された。専用GET probeは原Cookieを変更せず、認証値・個人データ・実画像を出力しない。メモリ上のCookie parseでCRLFのCRが残る不具合を合成回帰で再現し、通常text fileと同じ改行処理へ修正、12 offline検査と独立レビューを通した。修正後homeはHTTP200（TTFB1231.299 ms / 全本文取得1271.653 ms）だったが、ログイン済みを確認できず停止した。追加の固定bool診断と14 offline検査で、保存認証Cookieは記録上期限切れ、事前prepared requestに認証Cookieがないことを確認した。元Cookieの前後比較は全試行一致。ランキング/作品/画像には進んでおらず、実API画像取得や実データUI接続の成功とは扱わない。次の実API条件はローカルCookieの更新。詳細は`REAL_API_MEASUREMENT_2026-10-22.md`。

ユーザーの「コードレビュー後、問題なければマージ」に基づき、実装担当と別の3担当が基盤・ビューア・DB/計測をレビューした。基盤PR #1は `7125009` を `bdefa877617fc0dc2632e8de5801f197f0caa41f` としてmainへmergeし、そのmain CI3ジョブも成功。PR #2は原版と異なるviewer境界とfocusを修正して `43d0c64bd7acb6f96f387f9ff3639478417c278f` へ更新し、同SHAのCI3ジョブとnative受入後、`8df86671bbb0d7c4639e8dbb07687cb792f39566` としてmainへmergeした。全製品の受入完了を意味するmergeではない。
`43d0c64` だけをarchiveし、専用srcのimport先を確認してWindows onedir/noconsoleを再ビルド。別フォルダーのexeで10:29 JSTに、viewerのタブ内境界・通常closeの元カードfocus・学習への離脱・キー判定/Undo・ランキング復帰focus・最小化/復帰・通常終了を確認した。観測8プロセス終了、ポート解放、強制cleanupと予期しないconsole/page/process-log errorなし。archiveは132試験成功。175% DPI限定であり全DPI・別PCの受入ではない。
実Edgeの一覧/学習/画像競合3scriptが成功。画像/操作競合は16項目。1280×720/1920×1080でviewerとタブ内領域の一致、header/navの可視と操作、画像contain、詳細による画像領域の不変、検索/100件/小カード/DOM/scrollのタブ往復保持を確認し、全画面PNGを目視した。通常closeとタブ離脱を分け、古いrAFが新しいfocusを奪わないことも検証。独立再レビューに重大残件なし。詳細は `VIEWER_BOUNDARY_REVIEW_2026-10-22.md`。

学習DBはPR #3へ分離。古いUndoが別接続の新しい判定を消す競合を、履歴更新triggerによる世代と`BEGIN IMMEDIATE`内の照合/取消で拒否する。同値・ABA・旧writer・失敗時不変・元集計互換に加え、世代読取直後の別writerがlockされる試験を確認。constructor以外115メソッドのAST一致を再確認。UI修正を取り込んだ `22519a8ba388dc3ef60dc03dabf7a375395af2d5` のDB固有diffは、独立レビュー済み `7455ac3` とバイト単位で一致した。同headのCI3ジョブ（run 39706179497）と統合先main `8df8667` のCI3ジョブ（run 39706266732）成功後、10:41 JSTに `77d9761d7f7a73ce2e61fe83aeca1b740d921537` としてmainへmergeした。UIは引き続きメモリ台帳、operation IDの永続冪等性は未実装。

旧FletとUI `3947c95` の新版で、共通の合成PNG・1920×1080物理client・175% DPI・warmキャッシュ・600ms入力間隔・同じscreen取得方式による30遷移がそれぞれ完走した。観測p95はFlet432.402ms、新版227.204ms。ただしcapture中央値約195〜197msと表示画像面積・元shell・配布方式の差があるため、製品の速度改善率とは扱わない。private bytesのプロセス群合計はFlet376.25MiB、新版578.11MiB。詳細と失敗試行は `NATIVE_OBSERVATIONS_2026-10-22.md`。

PR #4はレビュー済みコードと受入記録を `95e4fbc636419d78ffbebc94a6fa575aea53f171` に統合し、同head CI3ジョブ（run 39707235390）とPR #3 merge後main CI3ジョブ（run 39707106962）成功後、10:46 JSTに `36040ee73c13fc22e5686ff795e617da270e9a32` としてmainへmergeした。そのmain CI3ジョブ（run 39707677025）も成功。

次の独立単位では任意の `screen-gdi` 取得方式を追加。同じUI43d0c64の静止画を順序交替で20組比較し、全RGB画素が一致した。最終実装の検査込み取得p50はPillow220.079ms / GDI115.816ms、コピーだけは130.109ms / 26.959ms。校正3全画面の目視後、同nativeで30/30ページ遷移を完走（観測p50 115.605ms / p95 123.451ms、capture overhead p50 92.520ms）。測定器の改善であり、旧新版アプリの速度比較には換算しない。
GDI復元失敗0/-1/Noneの回帰と資源回収を修正し、独立担当が修正前RED→修正後限定47件成功を確認。統合210件 / 7.40秒も成功。GDI object計数0→1→0の自己確認、取得前後0→0、専用8プロセスの通常終了と両ポート解放、元ソースhash一致を確認した。詳細は `GDI_CAPTURE_REVIEW_2026-10-22.md`。

**段階3全体は未完。** viewerのタブ内境界・離脱・focusは今回受入済みだが、原shell全体と画像面積/配布条件まで揃えた性能比較、cold、全DPI/別PC、残り6画面は未完。UI132試験と、DB21・比較準備27を含むPR #4時点の統合180試験を混同しない。PR #4時点は180 passed / 7.10秒、GDI追加後の210件は上記の別実行。フェーズ判定は `PHASE_GATES_2026-10-22.md` に集約し、未実施を合格へ変えない。先行の30遷移測定後の専用Flet4プロセスと新版8プロセス、今回のnative検証8プロセスはいずれも通常終了した。
以下は公開済み基盤の過去記録であり、最新版のnative配布・全面移植の完了を示すものではない。

## 状態

公開pushと新アプリの実画面操作は承認済み。初回 `500902a` と、画像配信・学習状態・読取アダプタの `160dbbe` を公開済み。
この後続単位では、合成データによる一覧/ビューア/学習の実ブラウザ操作と、Windowsの最小配布物を検証した。
元のPixivVaultの作業ツリー・DB・画像・Cookie・環境は変更していない。UIは依然として架空120作品のみで動く。

**全面移植・外観一致・高速化の受入は未完。** ランキングと学習の代表経路は動くが、他6画面、実データ書込み、元shellと配布条件まで揃えた性能比較は未実施。

## 担当

- worker: 画像/資源寿命、学習、読取互換、ブラウザ検証、Windows終了監視の限定実装と独立レビュー。1ファイルの編集を担当範囲に分ける。
- prime: 接続・画像イベントの切分け、ブラウザ/WebViewの実操作、ビルド、統合確認、記録、Git。
- 上限8workerの承認はあるが、この再検証では通常2件並列で分担した。利用枠の消費量は未計測。

## 検証

環境: Windows / Python 3.13.11 / NiceGUI 3.18.0 / pytest 8.4.2 / Playwright 1.64.0 / Edge・WebView2 155.0.4283.62。
新repoの専用venvを使用。バージョン一覧は `requirements-lock.windows.txt`。全間接依存の固定一覧を保存したが、別PCでの再構築は未検証。

| コマンド | 実測結果 |
|---|---|
| `python -X utf8 -m pytest -q --junitxml=artifacts/final-pytest.xml` | 2026-10-22 02:55頃、122 passed、4.25秒、exit 0 |
| `python -m pip check` | No broken requirements found、exit 0 |
| `python -m compileall -q src tests tools` | exit 0 |
| `python tools/check_browser_suite.py --channel msedge --output artifacts/browser-suite-final` | 02:51–02:52 JST、一覧2サイズ・学習13項目×2・画像故障/遅着7項目・2ページ独立性を通過、予期しないconsole/page error 0 |
| `python tools/run_browser_checks.py --channel msedge --output artifacts/browser-integrated-final` | 一覧/学習/画像競合の3scriptが全てexit 0、試験用サーバーのポート解放を確認 |
| PyInstaller `--onedir --noconsole` の専用出力ビルド | exit 0。独自画像コンポーネントのJS同梱・元ソースとの一致を確認 |
| 配布exe `--check --check-output <絶対JSONパス>` | demo / synthetic / native requested・available true、exit 0 |
| `python tools/check_native_window.py --exe artifacts/restart-standalone/native-app/PixivVault-NiceGUI.exe --inspect-ui --output artifacts/restart-standalone/native-acceptance.json` | 02:55 JST、別フォルダーへコピーしたexeの表示・画像・ビューア・学習/Undo・最小化/復元・通常終了・子プロセス/ポート解放を通過、強制cleanupなし |
| `git diff --check` | exit 0 |

03:15 JST追補: 選択中のカードサイズにもQuasarの既定文字色が残り、青背景に青文字になる表示を実exeの画像で発見した。サイズ・学習判定・Undoの色はテーマだけで制御するように修正。
`python -X utf8 -m pytest -q tests --ignore=tests/test_legacy_baseline.py --junitxml=artifacts/final-ui-pytest.xml` は **124 passed / 4.31秒 / exit 0**。
除外した1ファイルは別作業中の未コミット旧Flet試験であり、このPRの124件には含まれない。
同じ修正後に `python tools/run_browser_checks.py --channel msedge --output artifacts/final-ui-browser` を実行し、一覧・学習・画像故障/遅着の3scriptがexit 0。
サイズ3段階の選択中文字と学習4判定の文字が、実ブラウザで既存の `text-main` 色になることを追加検証した。

ブラウザの比較条件は1280×720と1920×1080。50サムネイル、50→100件で既存DOM保持、カード大中小、検索/clear、作品内のページ送り、詳細とEsc二段、一覧へ戻るscrollTop保持を確認した。
学習は4判定、Undo、古い世代の拒否、通常ショートカット、repeat/IME/入力/ダイアログ/非activeでの抑止を確認した。
故障注入した画像のHTTP503/404だけを期待エラーとして記録し、他のconsole/page errorは失敗にする。
試験前後の製品ソースハッシュは一致している。生の報告・画像はignoredの `artifacts/` に保存し、gitには同梱しない。
122件の実行時間やPlaywrightの待機時間は、アプリのp95性能値ではない。

native実測のWebViewクライアント領域は1907×1044、`devicePixelRatio=1.75`。これを100/125/150%の全条件合格へ読み替えない。
本番ジョブ・トレイ・URI・別PCへのインストール・長時間運用は今回の最小配布試験に含まない。

実証して修正した箇所:
- Undo時、キャッシュ画像の `currentSrc` が以前のfragmentを返し、画像readyが回復しない不具合。要求ごとの実DOMノードとdecode完了、HTTP資源の一致を照合し、捕捉した要求URLを通知する。サーバー側の世代照合は緩めていない。
- Quasarの既定文字色と選択中タブ背景が共に青になる不具合。ナビゲーションの既定colorを外して既存トークンで制御し、computed styleとスクリーンショットで読める状態を確認した。
- 並行closeによる資源の二重解放。closeを排他し、並行実行試験で一度だけ解放されることを確認した。
- native終了監視で、プロセスの照会失敗を終了済みと誤認する経路。PID/作成時刻・列挙失敗・待機戻り値を確認し、不明を監視失敗として出す。故障注入試験と実exeの終了確認を行った。
- 並行統合で重複したCIジョブキー。ブラウザジョブを1つに統合し、重複を検知する回帰試験を追加した。

GitHub ActionsではUbuntu/Windowsのpytestと、専用Chromiumによる実ブラウザ試験を別ジョブで実行する。
`ab00367` のWindows/UbuntuのpytestとUbuntu Chromiumの操作試験は全て成功（run `39649991323`）。その後の文字色追補の最新CI結果はPR #1のChecksで確認する。古いコミットの成功で代用しない。

## 製品受入の未完

原shellと配布物まで揃えた速度/メモリ比較、全画面の外観一致、実ライブラリ接続、実データ学習の保存、ダウンロード/停止/再開、トレイ/URI/全DPIは未完。合成のwarm主画像比較と学習sandboxの保存・再起動は上記の受入範囲に限る。
読取アダプタは一時fixture DBでの互換確認まで。任意の本番DBを暗黙に開く入口は作っていない。
旧Fletの限定ソースを合成fixtureで隔離起動する準備は別単位で進めている。旧Flet隔離launcherはランキングtabのみ。NiceGUIの合成ライブラリは作者詳細まで追加したが、原製品shell全体の受入や性能値には扱わない。

## 次の受入

次は他DPI・最大化/長時間復帰の実画素検査、初回表示/cold、連続入力、原shellと配布条件まで揃えた比較を進める。主画像比較には約91〜96msの取得負荷中央値があり、短い応答の観測分解能には限界がある。100/125/150%と別PC、残り6画面も独立した必須ゲートとして残す。
マージは独立レビュー・該当headのCI・変更に必要な実画面/実機受入後に行い、先行PRのmerge後は後続のbaseと統合treeを照合する。実APIの追加承認は専用の読取計測に適用し、製品の本番DB接続や既定UI置換、フェーズの未達ゲート省略へ広げない。
## マイブックマークの合成UI（2026-10-22）

段階4のマイブックマークは、原版との照合・純状態モデルPR #12の後、合成110件/ローカルDL済40件の切替可能なサブビューへ進んだ。作者一覧状態と独立した選択・明示50件追加・合成再読込と取得失敗時のキャッシュ維持・共有ビューアのページ移動とタブ往復を扱う。UIレビューでbadgeの配置/古い操作世代/同じIDでのbadge差分/旧Moreを修正し、初回実画面のサムネ高さ0を描画構造で修正した。375pytestと2画面サイズの実Edge4系統が成功。詳細は `BOOKMARK_UI_REVIEW_2026-10-22.md`。本番ブックマークのON/OFF・実API DTO/画像配信・ダウンロードは未接続。
## Phase8 最終ローカル受入（2026-10-24 JST、PR/CI統合前）

- 対象: `phase8/legacy-diff-sync`。旧DB/画像/ZIP/Cookieの原本には書き込まず、合成fixtureと新managed rootのみで検証。Phase7 PR #28のmerge済み実績とは区別する。
- RED→GREEN: `final20` の shutdown POST後 `process.wait(timeout=15)` が間欠的にTimeoutExpired。Windows Proactor `WinError 10054` のログ、NiceGUI/Uvicornの接続drain既定無制限を調査。Phase8 checker専用 `timeout_graceful_shutdown=5`、親wait25秒、CI証跡のnonce/argvを含む例外文言の固定化で対処。修正後Edge二連続各4/4、Chromium4/4、加えて最終source変更後Edge4/4、すべて自然終了・ポート解放。
- RED→GREEN: 128件を受理した後、owner別一時pinが残って次バッチに進めなかった。受理済みauthor intentに限ってpinを解放し、未完了job IDは保存済みと偽判定せず次previewの選択から除外。140件全件NEW・最初128件は未保存でpendingのまま、次12件を受理する回帰テスト追加。一般bookmark129件以上は解除対象外。JobService/managed intentのdurable保存は保持。
- 最終変更に対する全pytest: **1180 passed, 1 skipped, 1 warning / 67.53s**。skipはsymlink権限、warningはFastAPI/Starletteテストclient互換警告。
- Phase8最終Edge: `artifacts/phase8-legacy-author-diff-edge-final-after-pending-20261024/result.json` はsuccess=true、1280×720/1920×1080の初回+再起動4/4、normal_exit4/4、ports_released4/4。合成fixture旧DB/media SHA不変。修正後先行 `edge-shutdown-bounded-20261024-a/b` も両4/4。Chromium `chromium-shutdown-bounded-20261024-b` 4/4（初回ローカル実行はChromium未導入で失敗、Playwright Chromium導入後GREEN）。
- Windows最終ビルド: `tools/build_windows.ps1` PyInstaller onedir/noconsole成功。dist全体を `artifacts/phase8-native-copy-final-20261024` へコピー、**1050/1050ファイル SHA256一致、不一致0**。コピーEXE SHA256=`498296CA10C107B8CC039BB3558FFE605B9130ED2E0B6C9E187B32ACCAD06386`。`artifacts/phase8-native-copy-final-acceptance-20261024.json`: status=passed、画像decode/検索/閲覧/UI、最小化/復帰、8所有PID自然終了、port release、forced cleanup0。
- CoS worker-1独立read-only最終差分レビューは**APPROVE / P1=0, P2=0**。確認対象はshutdown、旧GET、nonce、owner別pin、140件pending二段バッチ、CI browser。実Cookie限定GETと実データでの本番配布受入は未実施。実データ完全互換・全移植完了とはしない。
- 本節はローカル受入であり、Phase8 PR/head SHAのWindows/Ubuntu/Chromium CIとmerge後main CIは**未受入**。結果取得後にGitHub実績を追記する。
## Phase9 managed scheduler（2026-10-24 JST、未完成）

**後任による最新ローカル受入（以前の全記録より新しい）**: 再開時点の未コミット現物をCoS Coreで照合・保持し、全差分の独立read-onlyレビューで発覚した追加P2を順に修正。最新結果は **1213 passed/1 skipped/1 warning**、Edge **4/4**・製品SHA **59/59一致**、Windows別snapshot SHA **117/117一致**・別rootコピー **1050/1050一致**、Native WebView実操作 **passed**（正常終了、所有8 PID終了、強制cleanup0、port解放、エラー0）。独立レビュー最終 **APPROVE / P1=0 / P2=0**（静的）。レビュー、具体的な追加修正、成果物SHA、証跡の相互対応・失敗履歴は `docs/PHASE9_FINAL_LOCAL_ACCEPTANCE_2026-10-24.md`。PR/head CI/merge/main CIはこの段階では別ゲート、実Cookieも未受入。旧スクリーンショット事故前bytesは未復元。Phase9の局所完了をPixivVault全体の完成とは呼ばない。


- **最新引継ぎ準備・2026-10-24:** `docs/PHASE9_RESTART_HANDOFF_2026-10-24.md`を追加し、`docs/ORCHESTRATOR_START_PROMPT.md`冒頭、`docs/ORCHESTRATOR_HANDOFF.md`冒頭に最新状態を追記。ユーザーはこの文書/プロンプトを後任が読んで再開できる形にし、文書だけを対象ブランチへ非force commit/pushするよう明示承認。Phase9コード/テストのdirty差分はこの文書commitへ混ぜない。最新受入は下記P2 remediation 1204/1、Edge4/4、native1050 SHA一致だが、修正後の全差分独立レビューは未完。後任受領・実再開も未確認。

- **2026-10-24 最新候補（未commit、レビュー/CIゲート未完）**: 全ソース自監査で発見したpartial→saved履歴集約のP2も修正済み。最終コード full pytest **1203 passed / 1 skipped / 1 warning, 67.95s** (`artifacts/phase9-final-partial-history-recovery-20261024/full.xml`)。skipは`tests/test_managed_downloads.py:251`のWindows symlink権限、warningはFastAPI/Starlette `httpx` TestClient deprecation。Native checkerのrun固有スクリーンショット名変更後の回帰は **84 passed** (`tests/test_native_check.py tests/test_packaging.py tests/test_release_tree.py`)。
- **Phase9 Edge final partial-history-recovery**: `artifacts/phase9-managed-scheduler-edge-partial-history-recovery-20261024/result.json` success=true、1280×720/1920×1080×初回/再起動4/4、scheduler設定→dispatch→CAS receipt/history、画像decode/focus、再起動時原寸GET0、console/page errors0、foreign requests0、normal exit4/4、port release4/4。全59製品ソースhashがlive worktreeと一致。70作品合成ledgerは70/70 dispatch items `saved`、active generation 70、deferred 0。実Cookieではなく合成fixtureのみ。
- **Windows final partial-history-recovery source**: `tools/build_windows.ps1`でPyInstaller onedir/noconsole build成功。別root `artifacts/phase9-native-copy-partial-history-recovery-20261024`へ**1050/1050ファイルSHA一致、mismatch=0**、EXE SHA256=`65ADBBF0D3421024296155CEDB0363B64D9EC5A916A404BAA1906AF864C34E8D`。`artifacts/phase9-native-partial-history-recovery-acceptance-20261024.json` status passed、GUI/handshake/cards50/image decode、viewer/library/downloads操作、最小化/復帰、正常終了、8所有PID自然終了、forced cleanup0、port release、errors0。Native checkerは`output_path.stem + "-webview-ui.png"`を使い、6枚全てrun固有stemへ出力する。
- **独立レビュー発見P2→修正・再検証中**: 古いactive receipt再照合だけでは、同一作品の手動ジョブが詳細GET中に保存を完了するTOCTOUを完全に防げなかった。`ManagedPixivDownloads.work_lock(work_id)`を追加し、手動受付、schedulerの最終pending/receipt確認→detail GET→enqueue、workerのactive receipt検査とCAS publishで共有する。workerはネットワーク/画像I/O全体でロックを保持せず、active pointerの読取/commit区間のみロックし、同じ作品の別client受付の冪等性を保つ。`test_work_lock_is_shared_and_serializes_same_work`追加。focused52 passed/1 skip、最終全pytest1202 passed/1 skip/1 warning、Edge/nativeは上記の現ソースでGREEN。
- **証跡事故（2回目の上書きも記録）**: `artifacts/webview-ui*.png`の共通名6ファイルは、最初の事故で上書き後に退避したものを、履歴保持native reacceptanceの最初の試行でも再度上書きしてしまった。直後に現時点の6枚を`artifacts/phase9-native-history-preserve-20261024/`へSHA一致でコピー。前のrun固有保存先`phase9-native-final-stable-acceptance-20261024`と比較すると、3枚は同一、3枚（library-authors/detail/download-jobs）は別版が保存済み。初回事故前の元bytesは未復元、VSSは権限不足で未確認。checkerの実装をlive worktreeとnative snapshotの両方で修正し、最後のnative成功runはrun固有stemで撮影、共通名は以後触っていない。詳細は`docs/PHASE9_EVIDENCE_INCIDENT_2026-10-24.md`。
- **2026-10-24 P2 remediation受入**: P2-1〜6を修正。P2-1 favourite list更新transaction内で除外authorのdeferredを削除、P2-2 interrupted状態ではbacklog dispatchを抑止、P2-3 itemなしのdeferred-only slotをterminal `deferred`化、P2-4 failed aggregate historyのsaved上書きを禁止、P2-5 scheduler settings dirty flagでtimer refreshから入力を保護、P2-6 backlog失敗時に60秒wait、Edge checkerでdispatch_items全70 saved/deferred_work 0を直接assert。最終コード full pytest **1204 passed / 1 skipped / 1 warning / 61.09s** (`artifacts/phase9-final-p2-remediation-final-20261024/full.xml`)。skipはsymlink privilege、warningはStarlette/httpx deprecation。
- **P2 remediation Edge**: 最初のrun `artifacts/phase9-managed-scheduler-edge-p2-remediation-20261024/result.json` はchecker/source hash自己整合assertでRED（画面4ケース自体は動作し正常終了/port release）。runnerのbacklog失敗判定を明示的なdispatch成功戻り値に修正し、native build snapshotにも同じ差分を反映して再実行。最終run `artifacts/phase9-managed-scheduler-edge-p2-remediation-final-20261024/result.json` **SUCCESS 4/4**、59製品ソースSHA live/build/Edge一致、初回original GET=6、restart GET=0、console/page/foreign errors=0、normal exit/port release 4/4。
- **P2 remediation Windows native**: snapshot `S:/Projects/PixivVault-NiceGUI-wt/phase9-native-build-p2-remediation-20261024` でonedir/noconsole rebuild成功。copy `artifacts/phase9-native-copy-p2-remediation-final-20261024` **1050/1050 SHA一致、mismatch=0**、EXE SHA256=`D630B570AC490EB19FF7C097F767512DBCF3D3961BF58B4092CCF89954897A0B`。native `artifacts/phase9-native-p2-remediation-final-acceptance-20261024.json` passed: UI handshake/cards/image decode/download interactions、minimize/restore、8 owned PIDs自然終了、forced cleanup0、port release、process log errors0。screenshots are run-specific.
- **独立最終レビュー未完**: P2修正後の独立worker起動はCoSCoreの「chat ... did not report back in time (no browser opened it)」で失敗。重複起動していない。前回worker-4の承認はshared per-work lock/race/lock-order範囲のみで、全Phase9 diffの包括承認ではない。P2修正後の全差分APPROVE/P1P2=0は未確定。Git差分はdirty保持、`git diff --check` clean。Phase9 PR/head CI/merge/post-merge CIは未実施。実Cookie・本番作者全件・実データは未受入、原版metadata backfill/Shaft/vaultは別機能として未移植。全範囲APPROVE/P1P2解消前にPR/mergeを行わない。

- 2026-10-24再開時（CoSCore現物確認）：Phase8 PR#29 MERGED / origin/main=`8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`、run`40001273903` Windows/Ubuntu/Chromium全SUCCESS。Phase9同HEAD・前回dirty9パス・PRなし・owned processなし。旧正本文書とPhase9契約を全文照合。
- 今回の未コミット改修：`ManagedScheduler.configured`で既存opt-in ledgerのみ再起動自動復元、`services`はmanaged resources準備後に再起動時runner復元。曖昧なinterrupted slotの確認操作を設定UIへ追加し、未完了JobService jobがあれば解除しない。runner停止を最大150秒待ち、120秒までのPixiv GET中に先にresourcesを閉じない。run status `done` を保存完了と誤称しない `queued` へ変更。focused36 passed/2.51s。これだけではPhase9完成ではない。
- **残る重大欠損**：50件超の候補の永続backlog・継続投入、scheduled accepted operationsと実際のJobService/CAS receiptを紐付けた保存完了/失敗履歴・通知、停止が150秒を超えた場合の安全な終了/再試行、これらを含むPhase9統合試験・最新ソース全pytest/Edge/Windows/独立最終レビュー・PR/headCI/mainCI。これらはGREENを主張しない。

- 2026-10-24続行（実装途中、未commit）: `ui/scheduler_view.py`、shellの定期チェックタブ、`services.enable_managed_scheduler()`、`managed_scheduler.submit_author_batch()`を追加して managed-only の設定保存・手動操作・完全GET→既存JobService受付まで接続。独立read-only workerは2client古い画面が最新設定を上書きするP1、旧DB内保存済み作品をNEWとして50件まで自動再取得するP1、起動時wakeでstartupチェックを落とすP2等を指摘。`configure_all`単一transaction/expected_revision比較・`LegacyAuthorBridge.observe_author(scan_id=None)`の読取済み行自動保留・startup wake修正で対応。修正後focused20 passed。全pytest1195 passed/1 skippedはそれら**最終修正前**の観測。Phase9専用Edge/Windows/独立レビュー最終GREENは未受入。
- 既存Phase8差分Edge `artifacts/phase9-scheduler-regression-edge-20261024/result.json`、作者差分Edge `artifacts/phase9-author-regression-edge-20261024/result.json`、旧ZIP Edge `artifacts/phase9-legacy-zip-regression-edge-20261024/result.json` はいずれもchecker成功・自然終了とport解放各4/4。ただしPhase9設定UIの総合受入ではない。
- 新たに確認した阻害: ON保存後アプリ再起動で schedulerタブを開くまでrunnerが開始しない、scheduler停止join timeout時の安全な資源解放、ambiguous slotの明示ack画面、50件超候補の継続処理、実保存結果に基づく履歴・通知。未解消のためPhase9 branchはdirtyのまま保護し、PR/merge保留。実Pixiv Cookie GETは今回行っていない。

- Phase8 PR #29はMERGED、head `13d897264c373705cfaf3ac56c2861934561e7d7` CI `40000094399` Windows/Ubuntu/Chromium全SUCCESS。main `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`、post-merge `40001273903`全3 SUCCESS。過去の「Chromium実行中」は途中状態であり現時点のREDではない。
- Phase9専用 `phase9/scheduler-managed` は最新main `8d55756...` から作成。元 `S:/Projects/PixivVault` のscheduler.py/gui.py/database.py等はread-only静的照合し、原版への書き込みなし。独立workerによる16項目の契約照合完了。`docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md`参照。
- 基礎スケジュール台帳・timerのみ新設。既定24h/起動時OFF、0/6/12/24/48h、設定revision CAS、UTC due、managed root内SQLite、process owner lock、停止と設定wake分離、二重tick/再起動後の不確定dispatch保留、明示ack。既存`JobService`自体には他種ジョブを混入させていない。
- 基礎回帰 `tests/test_managed_scheduler.py`: 14 passed（0.25s）。追加前の全pytestは **1191 passed / 1 skipped / 1 warning（74.94s）**。上記最新追加後の全件、実Edge、Windows final build、独立P1/P2レビュー、GitHub Phase9 PR/head/main CIは未受入。合成テストのGREENをPhase9製品全体のGREENへ外挿しない。
- **重大な未完**：本番managed favorite選択・完全GET安全判定→永続JobService enqueue、UIスケジュール設定/通知/履歴、クラッシュ後の動作復元、容量・競合、多client/SQLite障害、ネットワーク異常、Windows UI。原版メタ後埋めとShaft借用vaultは現NiceGUI未実装、別機能ゲート。全移植完了・Phase9 PR可能と主張しない。

## 2026-10-26 Phase11 M3 単一小説 fixture → 永続 JobService 接続 (実装準備済み / CoS未検証)

- 専用独立clone `S:/Projects/PixivVault-NiceGUI-m3-novel-integration-20261026`、branch `phase11/m3-novel-integration-20261026`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。共通base `31aad331846fc3d4c3aceac4f7a90fb61f05b111`。PR34のみを依存採用し、元branch/main/他候補は変更なし。commit/pushなし。
- 製品コード: `managed_novel_jobs.py` と `ApplicationServices` のfixture入口で enqueue→既存JobService operation/attempt/control→fixture本文照合→NovelExportStore→保存前receipt proof journal→path+SHA世代CAS→再起動照合を接続。`JobContext.publish` が停止/古いattemptとのpublicationをfenceする。イラスト/うごイラnamespaceは変更しない。
- 回帰コード `tests/test_managed_novel_jobs.py` を作成。アプリサービス経由3形式、pause/Stop/delete/retry、operation再投入、CAS、通常close/実プロセス強制終了後復旧、fsync不確定、整合性・owner・ロック競合を対象とする。**この担当はテスト/lint/compile/アプリ/GUI/GPU/実通信未実行**。PASS・製品完成の主張なし。
- 実行手順・期待結果・未検証/対象外は `docs/PHASE11_M3_NOVEL_INTEGRATION_2026-10-26.md`。SHA manifest/patch/書込み終了は専用 `artifacts/m3-novel-integration-20261026/`。CoS検証と独立レビュー待ち。M4画面/reader/ライブ取得/全移植受入は未達。


## 2026-10-26 P1 小説実用接続（専用候補・未検証）
取得/作者・bookmark選択→preview→queue→v2 bundle receipt→保存済みライブラリ→restartのコード接続を準備。v1読取互換を維持。実行承認待ちのため全pytest/構文検査/GUI/native/live GET/CIは未実行、独立監査待ち。詳細: docs/P1_NOVEL_PRACTICAL_2026-10-26.md。原M3/M4は別候補で保全。P2未移行。


## P1 小説実用接続 fixture自己検証 (2026-10-26T09:18:26.474887+00:00)

単一/明示章順シリーズの取得・TXT/EPUB/BOTH・画像表紙・bundle receipt v2・queue制御・永続libraryを接続。再起動時の表示順を固定。全pytest 2146 PASS/2 SKIP/0 FAIL、Headless Edge初回/再起動4/4成功。詳細はP1_NOVEL_PRACTICAL_2026-10-26.md。独立監査・配布native・live GET・exact-head CIは未完了、P2未着手。旧M3/M4不変。最終識別/差分/SHAはartifacts/p1-validation-20261026-r1/final/manifest.json。


## 2026-10-26 P2 うごイラ実用接続・独立候補

PR35 `50f8ae658a112f4db260ee979af97ee71e5cce93` 基線の `phase11/p2-ugoira-practical-20261026`。metadata→形式指定→durable queue→safe ZIP→フォルダー/GIF/CPU mp4v→receipt/CAS→保存/失敗表示→保存形式どおりのライブラリ再生→再起動を接続。P1/M3/M4は凍結維持、P1との統合なし。

最終自己検証: focused63 PASS、全pytest2018 PASS/1 SKIP/0 FAIL、Edge初回/再起動×2サイズ4/4。symlink権限SKIP、独立監査/native/live GET/exact-head CI/総合UX未実施。原GIF量子化・MP4平均FPS/圧縮の制約を保持し、元画像/delayはフォルダーで保全。詳細・失敗修正履歴・再現は `P2_UGOIRA_PRACTICAL_2026-10-26.md`。最終candidate SHA/diff/write-endは専用artifact final。正式受入とP3移行はdot判断。


## 2026-10-26 P3 専用共存候補・残差実装（P3未完）

- path `S:/Projects/PixivVault-NiceGUI-p3-coexistence-20261026`、branch `phase11/p3-coexistence-20261026`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。固定P1/P2から専用候補内へ共存。共有services/job/shellは単一writer、P1/P2/M3/M4は読取り保全。commit/push/main統合なし。
- 小説/うごイラを分離台帳のまま共通queue・保存・libraryへ接続。原公共ranking/タグ検索、library詳細filter、学習review/取消/復元、保存済み類似検索・推薦cache閲覧/設定sidecar、viewer個別managed保存、重複検出/計画を実装。原仕様対応・制限は `P3_COEXISTENCE_RESIDUALS_2026-10-26.md`。
- 全pytest **2313 PASS / 2 SKIP / 0 FAIL**、共存Edge **4/4**、学習review Edge **4/4**、library Edge成功。全て隔離fixture/headless loopback自己検証。symlink権限SKIPを権限変更で回避せず、独立監査とは扱わない。
- 具体的阻害: 原画像隔離/ZIP部分削除と不変receipt/CAS契約の衝突。検出・計画まで実装、移動/削除は未実装・未実行。新世代/tombstone、除外台帳原子性、scheduler抑止、旧ZIP互換の採用判断が必要。
- 他の実装残件: 作者/scheduler全media dispatch、実discovery取得/review保存/trend、推薦更新/boost、個人/premium ranking、原設定・Shaft/vault・tray/URI等。独立監査/live/native/exact-head CI/総合UX/全原操作分母は別の未達。P3閉鎖・P4進行を宣言せずdotへ判断を返す。
- 最終dirty/P3所有/SHA/全差分/入力保全/書込み終了は `artifacts/p3-20261026/final/manifest.json`、`candidate.patch`、`frozen-preservation.json`、`write-end.json`。再現コマンドは同残差文書と最終log参照。


## 2026-10-26 P3 非依存残差候補

`phase11/p3-residuals-20261026`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。固定前P3から別候補へ分離し、作者/scheduler全媒体、発掘/trend/推薦更新、原設定/cache、外部連携を接続。詳細と保存契約判断は `P3_NONDEPENDENT_RESIDUALS_2026-10-26.md`、最終hash/自己検証/write-endは `artifacts/p3-residuals-20261026/final/`。重複変更は保留。自動ZIP/ブックマーク配置のreceipt衝突、製品依存とnative/URI、独立監査/live/CI/UXを残す。結合画面再起動は自動承認レビュー拒否により未実行、別経路で代行しない。P4未移行。

P3 residual最終自己回帰: full-r2 **2502 PASS / 3 SKIP / 0 FAIL、exit0**。source snapshot照合を最終manifestへ保存。固定前P3 84/P1 28/P2 16/M3 7/M4 18ファイル不変。GUI探索3の起動は拒否で既知未実行、独立監査/native/live/CI/UXは未完。書込み終了と正確な候補SHAはfinal/write-end.jsonを正とする。


## 2026-10-26 P3 closure successor

専用候補 phase11/p3-closure-20261026: retry/初回取得/原URIと拡張の永続受付/作者new_only のコードを接続。最終候補の試験・構文・GUIは未実行、CoSへ引継ぎ。旧2502PASSを継承しない。原操作対応・拒否原文・未達・CoS手順は P3_CLOSURE_HANDOFF_2026-10-26.md。独立監査とP4移行は未完了。

## 2026-10-27 P3 managed intent native I/O 修正候補（CoS検証前）

`phase11/p3-intent-io-20261027`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。固定export-fixture-args候補178を保全して別候補へ分離。managed intentのnamespace準備/一時書込み/公開/読取り/再起動をnative I/O表記へ統一し、論理path・所有検証・v1/v2契約と未知データ/失敗stage保全を維持。公開メッセージを変えず4項目の型付きI/O失敗属性を付与。既存注入の実発火検査と深いpathでの受付/中断/再起動/衝突回帰コードを作成。実行試験・構文・GUIは未実行、旧5PASS/2FAILは継承しない。原票の2件目原因同一性と修正結果はCoS待ち。手順・未達は `P3_INTENT_IO_HANDOFF_2026-10-27.md`、集合SHA/差分/書込み終了は専用artifact finalを正とする。P3全完・独立監査済み・P4移行を宣言しない。

## 2026-10-27 P3 export GUI限定修正（CoS実行前）

p3-export-ui-fix-20261027へ旧intent-io182を固定コピー。1280のcatalog shrink/重なりを小説限定CSSで修正、1920の実表示「エラー」とchecker期待「失敗」の不一致を検査側で訂正。通常選択ID確認とRetry後DOM確認を追加。試験/構文/GUI未実行。原票・差分・CoS4ケース手順はP3_EXPORT_UI_FIX_HANDOFF_2026-10-27.md、最終SHA/書込み終了は専用final。P3全完・P4移行は未宣言。

## 2026-10-27 P3 export checker navigation（CoS実行前）

p3-export-navigation-20261027でcheckerだけを既存8件page/failed-only Retryへ合わせた。製品ソース不変、通常ページ移動とtab-failed/同一job IDのRetryを使用。reader/restartの静的接続照合、manifest読取りnative表記、ページ移動回帰を追加。試験/GUI/構文は未実行。P3_EXPORT_NAVIGATION_HANDOFF_2026-10-27.md参照。旧184保全、P3閉鎖/P4移行未宣言。

## 2026-10-27 P3 legacy ZIP初期化I/O（チャット試験前）

p3-zip-init-io-20261027へ固定navigation186から分離。原票96AC03A6…のarchives marker open失敗に対応し、legacy ZIP exporterの初期化/公開/再起動回復をnative I/Oへ統一。論理公開path・所有/重複root拒否・排他的os.link・未知データ保全を維持。深いpathと実発火付き中断/回復の7回帰コード作成。試験/import/構文/製品/GUIは未実行、原票2件と関連回帰をチャット担当へ。詳細P3_ZIP_INIT_IO_HANDOFF_2026-10-27.md。P3全完/P4未宣言。

## 2026-10-27 P3 作者heavy/light配置候補（未検証）

固定zip-init-io189を保全しp3-author-layout-20261027へ分離。専用projectionの実作者folder有無を新規intentへ固定し、lightではbookmarkのみ、heavyでは既存作者folderとbookmarkの静止画/小説/うごイラ配置を接続。旧intent互換・CAS不変・direct相対参照を維持し9回帰コードを作成。実行試験/import/GUIは未実施。ZIP更新/改名・ZIP作者skip・元master所属引継ぎ/作者非bookmark出力・物理隔離は依存判断/未接続としてP3_AUTHOR_LAYOUT_HANDOFF_2026-10-27.mdへ分離。P3全完/P4未宣言。

## 2026-10-27 P3 旧マスター所属・作者全媒体出力（未試験）

固定191からp3-author-output-20261027へ分離。明示旧マスターrootの直下folder所属を読取りで新intentへ引継ぎ、既存作者admission/job固有proofを検証して静止画/小説/うごイラの作者projectionを既存export jobsへ接続。作者出力settle後にbookmark所属を固定。旧データ/CAS/ZIPを変更せず同一job Retry/再起動の5回帰コードを追加。試験/import/GUI未実施、既存PASS継承なし。P3_AUTHOR_OUTPUT_HANDOFF_2026-10-27.md参照。ZIP更新/改名/物理隔離の保存契約判断、独立監査/原全受入/P4は未達。

## 2026-10-27 P3 独立レビュー3指摘の修正候補（未検証）

p3-export-admission-fix-20261027で最大folder選択+警告/行単位scan隔離、論理同一要求の初回確定intent再利用、旧unpinned intentのupgrade再受付互換を実装。新規7回帰と旧2期待を原版規則へ修正。静的読取りのみ、試験/import/GUI未実行。P3_EXPORT_ADMISSION_FIX_HANDOFF_2026-10-27.md参照。固定193/191/189保全、ZIP等の契約/P3閉鎖/P4未達。

## 2026-10-27 P3 ZIP作者skip追加回帰（未試験）

195のscan_authors項目隔離がZIP所属拒否を捕捉済みと照合し、製品二重変更なし。別p3-zip-author-skip-20261027へZIP作者23の後に正常作者24が完了・再scan非重複・旧ZIP不変となる回帰を追加。試験はCoS後継へ。P3_ZIP_AUTHOR_SKIP_HANDOFF_2026-10-27.md参照。

## 2026-10-27 P3 永続受付・作者依存の根本修正（未検証）

固定196からp3-durable-export-20261027へ分離。既存operations台帳でjob50件窓外の初回受付を保持し、旧intent互換/読取不明時保留を実装。作者依存を全体不明/作者別未確定で分け、同作者全媒体のsettleまでbookmark新規pinを保留。旧ZIP先確認、原版tie列挙順、理由/項目のqueue/failed表示と変化時警告を接続。新11回帰作成、試験/import/GUI未実行。P3_DURABLE_EXPORT_HANDOFF_2026-10-27.md参照。旧PASS非継承、P3閉鎖/P4未達。

## 2026-10-27 P3 ugoira診断自己汚染・MP4試験I/O（未検証）

固定198からp3-ugoira-diagnostics-20261027へ分離。新診断は入力外の所有/既存quota対象領域へstaging公開し、旧入力内診断はjob台帳binding/schema等の厳密互換検証で保全。sealed frames再利用/Retry/restartを阻害しない13回帰を作成。原票3件の実読/hash一致、MP4試験の277文字logical readをnative helperへ修正（製品codec無変更）。198の耐久受付/作者依存を全保持。試験/import/GUI未実行。P3_UGOIRA_DIAGNOSTICS_HANDOFF_2026-10-27.md参照、P3/P4未閉鎖。


## 2026-10-27 P3 history-recovery（実装・回帰コード、未実行）

固定200（022c54c9717d44d50dc003de1cff1213be9cfbc84604b0cbb975b5d7fe47747e）から別候補 S:/Projects/PixivVault-NiceGUI-p3-history-recovery-20261027 / phase11/p3-history-recovery-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babba。完了履歴と編集可能projectionを分け、毎scan全payload再hashを撤去。owned JobService v1→v2のtransactional terminal履歴と表示窓外error/stoppedの明示復帰、失敗リスト通常ボタン、旧intent読取不能の独立継続、通常fileの原版isdir互換、tuple/list proofsのJSON正規比較を実装。補助診断の不整合/ledger/linkを媒体_budgetから分離しwriter/input側の安全拒否を保持。既存combined容量上限と診断保全を同時に維持するため、満杯rootの蓄積問題は未解消で、予約枠/保持方針の判断を分離。上限変更/削除なし。新30予定ケースと元回帰をCoSへ渡す。pytest/import/構文/GUI/nativeは未実行、旧PASS非継承。詳細 P3_HISTORY_RECOVERY_HANDOFF_2026-10-27.md、最終manifest/差分/固定候補照合/write-endは専用artifact final。P3閉鎖/P4へは未移行。


## 2026-10-27 P3 retained-fixture（テストfixtureだけ修正、未実行）

固定203 / SHA0dc9248c0a982309df34c0192bfab1ab2bb8117a0902cd7a367455961ef31644を保全し、S:/Projects/PixivVault-NiceGUI-p3-retained-fixture-20261027 / phase11/p3-retained-fixture-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babbaへ分離。CoSの203PASS/1FAIL/1SKIPの唯一のFAILは、実QueueView._matchingが要求するartworksがテストSimpleNamespaceにないAttributeError（Retry未到達）。artworks=()をfixtureへ明示し、表示assertを追加。実JobService/SaveExports/実handler/同job attempt2の検査は保持。製品src/toolsは203と全hash同一、変更はテスト1・文書2。試験/import/GUI/buildは未実行でCoSへ。P3_RETAINED_FIXTURE_HANDOFF_2026-10-27.mdと専用finalのmanifest/製品対応hash/最小diff/固定旧候補保全/write-endを参照。診断容量・独立レビュー・実画面受入は別担当の継続事項のまま。


## 2026-10-27 P3 acceptance-compat（製品/回帰コード、未実行）

固定204（SHA5486a95c1a088d3b37523ce97f1fd3a24e7d3d592cb6f441a6dc564257a29ddb）から S:/Projects/PixivVault-NiceGUI-p3-acceptance-compat-20261027 / phase11/p3-acceptance-compat-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babba。native managed/demoのv1固定を独立schema/history helperで厳密v1/v2対応。再起動直後の退避候補復元、static50窓外の完了receipt/受付復元、作者依存の同期batch再利用と直接対象限定、既往完了作者の別作品継続・新世代同一作品の衝突前明示保留、正規v1退避診断互換、job単位診断故障/理由表示、不正作者/reparse後続継続を実装。25予定新回帰と既存強化を作成。試験/import/構文/GUI/buildは未実行。既存JobService v2は6専用所有DBに及びコード自体は204から不変、元データへ未実行。同一作品投影の更新方式、完全欠損旧identity、診断総容量は未解消で新方式/数値/削除は未採用。詳細P3_ACCEPTANCE_COMPAT_HANDOFF_2026-10-27.md、finalに旧候補保全/manifest/最小diff/write-end。203のCoS全結果/Edge成功とClaude不通過は基線証拠で新候補PASSへ継承しない。P3全閉鎖/P4判断は未達。


## 2026-10-27 P3 compat-fixtures（211のテスト3失敗補正、未実行）

固定211/SHA2be602b70795ced707780deb72ebe898a8f7d72909fd194a10c00882f2125289からS:/Projects/PixivVault-NiceGUI-p3-compat-fixtures-20261027 / phase11/p3-compat-fixtures-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babbaへ分離。CoS原票SHA照合と合成DB読取りで、作者2条件のoutputs predecessor/更新日不一致、static runnerの5項目receipt→3変数展開を確認。テスト1ファイルに正規author new generation fixtureとprovider revision取得、5項目展開を実装。元の更新/編集保護/50件窓/再起動assertと実callbackを保持、初期/更新の実bytes/provenance assertを追加。製品src/tools169ファイルは211と同一。試験/import/構文/build/GUIは未実行、原3件→同ファイル8件をCoSへ。元focused214PASS1SKIP3FAIL/全2846PASS4SKIP3FAIL/GUI各4/4は基線証拠のみ。P3_COMPAT_FIXTURES_HANDOFF_2026-10-27.mdと専用finalに原因/最小差分/hash/製品対応/旧候補保全/write-end。既存未採用方式・容量方針は不変更。


## 2026-10-27 P3 binding-closure（実装・新回帰、未実行）

固定212/SHA06c4fbf344c41ecac86e8da8f7ea1803054f110cf791d7ce337622d86467a6abから S:/Projects/PixivVault-NiceGUI-p3-binding-closure-20261027 / phase11/p3-binding-closure-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babba。demo nativeの良いbinding1行で偽alias/孤立行を隠せる穴とv1未検査を全行helperで修正し、managed nativeの既存拒否を保持。作者reconcileの不正spec/結合/IO/journal commitを行隔離、未知を推定せず正常出力継続、固定理由を表示。旧export fallbackの不正targetをI/O前拒否、v1完了metadataは既存3/5fieldとautoZIP構造を検証しmutable bytes再hashなし。新52予定回帰を作成し、試験/import/構文/build/GUIは未実行。managed_ugoira・JobService・212の8回帰と既存診断/深いpathは不変更。v1退避snapshot欠損のnative証明限界と既存未採用契約は未達のまま。P3_BINDING_CLOSURE_HANDOFF_2026-10-27.md、専用finalに全hash/最小diff/旧7候補不変/正確な独立再レビューsource範囲/write-end。自己点検は独立監査ではない。


## 2026-10-27 P3 completion-snapshot（テスト前提3か所のみ補正、未実行）

固定216/SHA43cd535c737ea6cb3928119b626d5f7162903dfc60e8457ea5aa7020f1ae2eeaを保全し、S:/Projects/PixivVault-NiceGUI-p3-completion-snapshot-20261027 / phase11/p3-completion-snapshot-20261027 / HEAD2cf4a7a7286dd728e8df8125094498a3622babbaへ分離。CoS原票3件のSHA一致を確認。影響227PASS/9FAIL/1SKIPの9FAILは不変の受付snapshot.result=Noneを使ったTypeErrorで、manifestの元受入assert未到達。テスト3行でwait_stateの完了snapshotを受取り、v1不正証拠4・編集削除保護3・autoZIP2の後続assertを全保持。製品src/toolsは固定216と同一、他テスト・待機・期待値は無変更。試験/import/構文/build/GUIは未実行、同ファイル15予定ケース（未collect）のCoS再確認へ。P3_COMPLETION_SNAPSHOT_HANDOFF_2026-10-27.mdと専用finalに最小diff/hash/製品同一性/旧候補不変/write-end。旧GUI等のPASSを新試験結果へ自動継承せず、既存未採用契約とP3全体未達を維持。


## 2026-10-27 P3 L13 card size (implementation, untested)

Shared grid_card_size_mode persistence and three-view controls added in p3-card-size-20261027. Original caps 6/8/12, default large. CoS fixture and GUI verification pending. See P3_CARD_SIZE_HANDOFF_2026-10-27.md. P3 remains open.


## 2026-10-27 P3 L13 card-style fixture（未実行）

固定219のCoS 29 PASS / 1 FAILを受け、str(style) のCSS文字列期待をNiceGUI Style辞書のプロパティ検査へ修正。実recommend gridの作品存在とmedium 8→small 12、元の選択・ページ保持を検査。製品・toolsは219と同一。pytest/import/build/GUI未実行。P3_CARD_STYLE_FIXTURE_HANDOFF_2026-10-27.md参照。L13/P3受入は未完。


## 2026-10-27 P3 S21 theme toggle（実装・未検証）

固定220から別候補へ分離。原版の起動時darkとヘッダー明暗切替に合わせ、client別テーマ・画面色・入力欄の追従を実装。テーマを永続設定には追加せずL13カードサイズを保持。回帰コードを作成、pytest/import/構文検査/build/GUI未実行。CoSの全画面・再訪/再起動・L13影響確認と独立レビュー待ち。P3_THEME_TOGGLE_HANDOFF_2026-10-27.md参照。


## 2026-10-27 P3 S21 theme fields（実装・未検証）

固定223のライト入力白文字を受け複数行propsに残ったdark指定11個を除去、Quasarテーマ継承へ統一。実媒体View・全UI props回帰と媒体rootの表示待ちを行うCoS向けDOM checkerを作成。原票のugoiraラベル下にnovel欄があり原因の媒体別判定には注意。実行/import/構文/build/GUI未実施。P3_THEME_FIELDS_HANDOFF_2026-10-27.md参照。S21/P3未受入。


## 2026-10-27 P3 うごイラ形式ラベル（未検証）

固定228より別候補を作成し、CoS報告の「保存形式」省略を対象にselectの折り返し可能な最小幅を追加。既存形式・テーマ継承を保持し、実ViewとDOMで3選択肢とラベル幅を確認するコードを追加。pytest/import/構文/build/GUI未実行。P3_UGOIRA_LABEL_HANDOFF_2026-10-27.md参照、S21/P3未受入。


## 2026-10-27 P3 S21 field locator（未検証）

固定229のCoS原票と専用成功copyを照合し、納品checkerのq-field親探索2箇所と媒体ボタンのdownload内scopeを修正。元assertを維持しDOM親/自身/子孫/不存在の回帰コードを追加。製品srcは229と同一。pytest/import/構文/build/GUI未実行。P3_FIELD_LOCATOR_HANDOFF_2026-10-27.md参照。S21/P3未受入。


## 2026-10-27 P3 X04 dedup thumbnail size（未検証）

固定231を分離し、原版小88/中144/大200px・既定中を重複画面へ追加。選択revisionを変えず要素を更新し、画像なしの代替表示も同寸法。実Viewの選択・5枚制限・1枚残し・再描画を回帰コードへ追加。pytest/import/構文/build/GUI未実行。P3_DEDUP_SIZE_HANDOFF_2026-10-27.mdをCoSへ。X04/P3未受入。


## 2026-10-27 P3 L12 managed capacity（未検証）

固定233を分離し、active managed receiptと画像の実byte安全読取、先頭50作者の非同期測定とsession内反映、検索/画面/世代変更時の結果破棄を実装。旧フォルダー/ZIP・永続cache対象外。pytest/import/構文/build/GUI未実行。P3_MANAGED_CAPACITY_HANDOFF_2026-10-27.md参照。L12全体/P3未受入。


## 2026-10-27 P3 L12 managed capacity fixture（未検証）

固定237から別候補へ分離。CoS 47PASS/1FAIL/1SKIPの対象testへ保存後の正規refresh_library_snapshotを追加。作者91の実byteと画面反映assertを維持し、製品src/toolsは固定237と同一。pytest/import/構文/build/GUI未実行。P3_CAPACITY_FIXTURE_HANDOFF_2026-10-27.md参照。旧folder/ZIP・永続cache・独立reviewは未完、L12台帳加点なし。


## 2026-10-27 L12 managed表示分離・status失効修正（未検証）

固定238から別候補。managed実測を専用session cache/別ラベルにし、元snapshot author_capacitiesと通常容量ソートの上書きを撤去。reload/画面失効時のstatusを即消去し遅延成功/失敗の旧status復活を拒否。実測API不変更、回帰を追加。pytest/import/構文/build/GUI未実行。P3_CAPACITY_DISPLAY_HANDOFF_2026-10-27.md参照。33/152据置、独立レビューとCoS確認待ち。


## 2026-10-28 L12旧容量 中間候補（未完・未実行）

固定240から分離。注入SQLiteの成功値/0/世代CAS部品と旧作者候補の原版選択規則・合成回帰コードを作成。旧root探索・実測・service/UI・再起動/世代失効への接続は未実装。pytest/import/構文/build/GUI未実行。P3_CAPACITY_LEGACY_PARTIAL_2026-10-28.md参照。台帳変更なし。


## 2026-10-28 L12旧容量 明示注入接続（未検証）

固定245から分離。明示rootの旧folder/ZIP実測、rootを結び付けた注入SQLite cache、service/LibraryViewの計測・復元・capacity sort接続、leaseによる状態失効後publish拒否と合成回帰を作成。通常起動の設定経路・実データ適用は未実装。pytest/import/構文/build/GUI未実行。P3_CAPACITY_CONNECTED_HANDOFF_2026-10-28.mdに検証範囲とlock待ち/走査中変更の限界。L12/P3未受入、台帳据置。


## 2026-10-28 L12通常旧構成接続（未完・未検証）

固定248を分離。旧read-only library rootと専用preferences rootを既存構成から消費し、所有marker付き容量SQLiteを開いて既存旧容量serviceへ接続。合成通常構成/再起動/未知cache拒否の回帰コードを追加。旧とmanagedの同時構成排他・旧ZIPを含む重複排除契約・SQLite待ち時の非block取消は未解決。pytest/import/構文/build/GUI未実行。P3_CAPACITY_COMBINED_PARTIAL_2026-10-28.md参照。L12/P3未受入。


## 2026-10-28 L12取消競合（未完・未検証）

固定250を分離。合算撤回を反映、SQLite書込み中cancelのUI待機を合成cacheで検出する未実行回帰を追加。非blocking取消と失効後commit防止の同期実装・実SQLite回帰は未完。製品変更なし。P3_CAPACITY_CANCEL_PARTIAL_2026-10-28.md参照。L12/P3未受入。


## 2026-10-28 L12取消とcache commit決定（未検証）

固定252分離。SQLiteバッチtransactionの決定直前にleaseを短時間sealし取消と直列化、決定前取消はrollback、決定後はSEALEDから物理成功/失敗/不明を区別。UIはworker終端までbusy、失効画面への結果反映なし。原版に強い物理取消条文は検索範囲で未発見だが決定後の取消要求後にはcommitし得る点を明示。実SQLiteの競合・失敗・再起動・UI・close回帰を作成。pytest/import/構文/build/GUI未実行。P3_CAPACITY_LEASE_HANDOFF_2026-10-28.md参照。独立レビュー・CoS・L12/P3未受入。


### 2026-10-28 PV-B 作品詳細・作者一覧 接続候補

- 固定259を保全し、`phase11/p3-viewer-author-20261028`で詳細遅延取得/タグ・数値/Pixiv入口/作者50件追加・再訪を実装。
- L12/D15の製品・fixtureは不変更。回帰コード・合成ランチャーを作成。試験/import/build/GUIは実行していない。
- `docs/P3_VIEWER_AUTHOR_HANDOFF_2026-10-28.md`と専用artifactsのmanifest/diff/WRITE_ENDを参照。
- 原寸・ズームの採用根拠、表示ページ単独保存のreceipt接続等は未達。PV-B/P3全受入・独立レビュー完了とせず、152操作台帳の算定は不変更。


### 2026-10-28 L12 初回容量cache復元の再登録

- 固定265/259を保全し、`phase11/p3-capacity-reactivation-20261028`でLibraryViewの失効後cache復元を再登録。旧応答ガードと計測終了待ち、0/Noneの区別は維持。
- 新規15ケース相当の回帰を作成、既存接続試験を正規再active順へ適合。試験/import/build/GUIは未実行。
- `docs/P3_CAPACITY_REACTIVATION_HANDOFF_2026-10-28.md`と専用manifest/diff/WRITE_END参照。機能受入・独立レビュー完了とはしていない。


### 2026-10-28 PV-B 表示ページの原画像保存・receipt接続候補

- 固定267を保全。個別DLを全作品queueから、表示ページ1枚の原画像/検証receiptへ接続。完全保存receipt/JobServiceは変更しない。
- 遅着UI拒否・重複/再起動再利用・失敗再試行の回帰コードとCoS合成GUI入口を作成。試験/import/build/GUI未実行。
- 原互換配置/last_backup・うごイラ個別GIF等は残件。`P3_VIEWER_PAGE_SAVE_HANDOFF_2026-10-28.md`参照。原152操作算定・P3受入は不変更。


### 2026-10-28 PV-B 個別保存の原配置・履歴・表示GIF 接続候補

- 固定271を保全して別候補 `phase11/p3-viewer-save-parity-20261028` へ実装。既存projectionへ原ON/OFF配置、部分保存の作者別履歴、表示中GIFの同一bytes保存を接続。原本と出力/履歴の失敗を分離し、再取得なしのRetryを用意。
- CoS共有のnative path DB再起動URIを修正、独立指摘の51作者超cache復元を全snapshotへ拡大。厳密DB検証と明示再計測先頭50件は維持。
- 原互換の全作品work-folder命名/旧履歴統合、既存更新・ZIP/改名/隔離契約は別残差。コード作成のみで原受入/P3完了・独立監査とはしない。
- 試験/import/構文/build/GUI/製品起動/外部通信は未実行。`P3_VIEWER_SAVE_PARITY_HANDOFF_2026-10-28.md` と専用manifest/diff/WRITE_END、CoS一括手順を参照。原152台帳算定は不変更。


## 2026-10-28 P3 候補283 CPU10失敗の修正（未実行納品）

固定283の原票208 PASS / 10 FAILを照合し、別候補 `S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028` へ修正。製品3ファイルで、SQLiteの読取URIに拡張Windows filenameを保持し、DesktopIntegrationの所有path比較とnative I/O表記を分離。DB identity/schema/operations/receipt/所有拒否を緩和しない。試験7失敗は正規全作品受付、v1/v2重複keyの実注入、shell fixtureのbackup_view初期化へ対応し元assertを保持。新長パス・特殊名・read-only・reparse拒否回帰も作成。

実装者はpytest/import/構文検査/build/GUI/製品起動/実APIを未実行。元285文字超の再起動実結果はCoS待ち。283の208 PASSを変更後へ自動継承せず、機能受入・P3完成・独立監査とはしない。全UI/L12/D15/新履歴とGIFの製品コード・GUI checkerは283同一、固定283/271/267/265/259は保全。詳細・CoS一括手順は `docs/P3_VIEWER_PARITY_FIX_HANDOFF_2026-10-28.md`、最終hash/差分/終了は `artifacts/p3-viewer-parity-fix-20261028/`。


## 2026-10-28 P3 未計測作者の自動容量計測（コード納品・未試験）

固定285（CoS CPU246 PASS、通常GUI4/4、309文字root GUI2/2）から別候補 `S:/Projects/PixivVault-NiceGUI-p3-capacity-auto-20261028` を作成。原再設計仕様§2-5/§3と固定Fletのshown作者→_ensure_capacityを照合し、既存cache復元の後に、表示中の未計測作者だけを順次計測・成功ごと専用cache保存へ接続。51人目以降も追加表示で処理し、cached 0/旧read-only cacheを保持、同visit失敗を反復せず、再訪で再試行。失効後の旧結果拒否とlease/CAS、managed別表示、通常容量sortを保持。製品変更はLibraryViewのみ。

新合成回帰と通常構成のCoS launcher、既存3fixtureのライフサイクル補正を納品。実装者は試験/import/構文検査/build/GUI/製品起動/実APIを未実行。旧候補・原データ不変更、独立監査と原152台帳L12行は別未達。詳細は `docs/P3_CAPACITY_AUTOMATIC_HANDOFF_2026-10-28.md`、全hash/差分/終了記録は `artifacts/p3-capacity-auto-20261028/`。


## 2026-10-28 P3 review completion candidate (implementation only)

Candidate derived from fixed 292. Ugoira retained receipts, extension retained retry, ZIP accepted proof, staged static publication, ranking stale requests/personal context, vault queue/session checks and thumbnail hardlink checks changed with synthetic regression code. No test, GUI or product execution by implementer. Review items 5 and 9 remain partially unresolved: existing partial final files cannot safely be classified as owned without proof, and synchronous request handoff retains a guard/send scheduling gap. See P3_REVIEW_COMPLETION_HANDOFF_2026-10-28.md for exact scope and CoS entry. No P3 acceptance is claimed.


## 2026-10-28 P3 export/vault recovery candidate (implementation only)

Fixed 293 was preserved. Added owned stage plus journal recovery for auto-ZIP interruption and selected ONLINE request generation fencing, with synthetic regression code. Tests and product execution not run. Historical unknown partial final files remain fail-closed; the strict synchronous send-start/lock race remains unresolved and needs a transport-level design. See P3_EXPORT_VAULT_RECOVERY_HANDOFF_2026-10-28.md. No P3 acceptance claimed.


## 2026-10-28 P3 warning compatibility candidate (implementation only)

Candidate from fixed 294 restores the original maximum-selection warning wording in both logs without exposing raw IDs, folder names or exception paths. Original failing assertions remain, and the log privacy regression now covers both logger paths. Tests and product execution not run; CoS and independent review remain. P2-5/P2-9 and P3 acceptance remain open. See P3_WARNING_COMPAT_HANDOFF_2026-10-28.md.


## 2026-10-28 P3 vault socket fence candidate (implementation only)

From fixed 295, default vault HTTPS now registers duplicate sockets before connect/TLS and invalidates them on lock; scoped requests reject unfenced adapters, preserve epoch across re-unlock, and report failed shutdown. Synthetic and Windows loopback regression code is included but not run. Windows dup/shutdown propagation and full TLS/lock boundaries require CoS and independent review. See P3_VAULT_SOCKET_FENCE_HANDOFF_2026-10-28.md. P2-9 and P3 acceptance are not claimed.


## 2026-10-28 P3 held-warning compatibility candidate (implementation only)

Candidate from fixed 298 restores 保存後処理を保留 in the safe opaque-identifier log without changing suppression or queue status. Original failed assert remains and a privacy/dedup regression was added. Test execution and independent review remain. See P3_HELD_WARNING_HANDOFF_2026-10-28.md. P2-9 and full P3 acceptance not claimed.


## 2026-10-28 P3 personal view loop fixture candidate (implementation only)

From fixed 299, two existing PersonalRankingView tests register the real pytest loop in NiceGUI core and drain actual background tasks before cleanup. Original assertions and product code are unchanged. CoS impact verification is required; TCP/TLS fence and broader P3 acceptance remain unverified. See P3_PERSONAL_LOOP_FIXTURE_HANDOFF_2026-10-28.md.


## 2026-10-28 P2-9 TCP/TLS revocation candidate

Fixed307 preserved. New candidate p3-vault-tls-revoke adds pending owner close, connected duplicate activation before TLS, bounded structural OS diagnostics, fixture registration bookkeeping and failure lifecycle evidence. Code/regression creation only; no pytest/import/build/server/GUI executed. CoS batch and exact evidence limits: docs/P3_VAULT_TLS_REVOKE_HANDOFF_2026-10-28.md. Actual Windows acceptance and independent review pending; operation ledger unchanged.


## 2026-10-28 P3 capacity fixture repair

Fixed308 retained. Long-path read-only SQLite URI now retains live WAL, synthetic thumbnail names follow original flat form, cancellation checker closes browser before server stop. Code and test construction only; no test/import/build/GUI/server execution. CoS handoff: docs/P3_CAPACITY_FIXTURE_REPAIR_HANDOFF_2026-10-28.md.


## 2026-10-28 P2-9 D1 cleanup race

Separate candidate serializes request-owned alias close and fence deregistration against invalidate; adds synthetic connected-dup-failure / concurrent-lock regression. Code and test construction only; CoS execution and independent review pending. See docs/P3_VAULT_CLEANUP_RACE_HANDOFF_2026-10-28.md.
