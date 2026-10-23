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
