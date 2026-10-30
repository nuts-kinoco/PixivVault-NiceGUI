# P3-354 Stage 5 — 最終統合監査・完成判定（2026-10-30 JST）

## 1. 結論（release fail-closed）

**完成: 不認定。main統合: 禁止。**
この文書は既存Stage1〜4の部分成果と、未完ゲートを同じ基準で突き合わせた途中監査であり、最終承認ではない。機械可読の一次ゲート表は docs/P3_354_STAGE5_RELEASE_GATES_2026-10-30.json。16個の必須release gateを PASS/FAIL/UNVERIFIED で管理し、FAIL・未検証・SKIPを成功として数えない。開始基準HEADは 8b016673795fe55e2319a6a7f5cc324ebf1c6556、branchは phase11/p3-vault-cleanup-race-20261028。現在の選択的コミット後のHEAD・CI・証拠は再照合して追記する。

Stage2/3/4に成功した局所試験があっても次の重要ゲートが成立していない。

- 正式Flet原152操作/479参照は現候補 **0 formal PASS / 152 INCOMPLETE**。証拠manifest欠落は意図的なfail-closed。旧履歴の33/43/76を現候補PASSへ継承しない。
- 実Cookieの低負荷GET5件と原本Cookie不変は既存合格だが、実作者差分DL→保存→ZIP→Vaultへ拡張できていない。
- U01初期フォロー中作者 vs 初期ブックマーク、X07物理move vs copy+明示moveの既定差が残る。データ保護を弱める仕様変更は明示承認まで実装しない。
- 現候補と原Fletの同条件件数・保存名・メタデータ・実画面・性能p50/p95/max・capture overhead・private bytesは未合格。古い隔離viewerの性能値は現製品へ流用しない。
- Windows実GUIは起動・最小化・復帰・終了の限定PASSのみ。全ダイアログ・DPI・GPU・tray・URI・初見本人操作の一気通貫は不足。
- OS依存の既存6 SKIP、原本全ファイルの**試験前**と試験後の同一性証明、全変更SHAの最終実装外独立レビュー、PR/head一致/全CI GREENが不足。

## 2. 全操作と原本資料のゲート

原本固定台帳 docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json を**読取専用**で集計した結果、152件の一意なoriginal_id、479件のbindingが存在。原条件が現行記録で未明示のもの137件、マウス/キー操作未入力129件、receipt pathなし129件、完全原scope reconciled=trueは0、v8 formal_pass_added=trueは0。これらは文書の不足と実機未実行を区別するための数字で、未実装152件と断定したものではない。

Stage3の合成D01リトライ、U01作者モード、X07 copy隔離・実体move/restore、ZIP保存とHTTP障害の限定成功、Stage4のGUI几何・合成ワークフロー成功を個別証拠として保持。各bindingが要求する原Flet開始状態、実操作、実観測、reviewer、checker/source SHA、read-only original SHAを正式候補へ一対一接続する作業が未完。現在の0 formal PASSは変わらない。

## 3. Stage4最新修正と独立指摘（実際の失敗も保存）

前回のStage4証拠は docs/P3_353_STAGE4_GUI_UX_PERFORMANCE_2026-10-30.md に記録。

- ランキング「未取得なのに該当0件」および日本語フィールドラベル省略のUX P2について、ui/ranking_view.py の説明文・mode/content/date欄の最小幅を修正。r7ではclippedTextLabelsの3欄が見つかりFAIL、r8で4 viewport×6tabの限定GUI PASSへ回復。旧不具合とテスト失敗を消さない。
- checker P2として必須タブSKIP、固定透明overlay、部分見切れ、scroll軸誤免除、入力欄の遮蔽が先の独立レビューで発見され、生成HTMLの実再現と修正後テストを保持。さらに同一Quasar q-field内部の透明overlayを無条件に免除する偽GREEN経路をStage5でRED再現した。checker側の同一field免除を標準装飾だけに制限し、追加後の tests/test_stage4_geometry_guard.py は **9 PASS**、最新 artifacts/p3-354-stage5-geometry-r9/result.json は **4viewport/24必須tab/80warm/success=true・exit0・正常終了/port解放・source不変**。合成表示であり物理paintの優位性は未証明。
- r9 result.json SHA256 3fc526816daf39cd84787b62c8de42b0b8c04e988e73997d897d72897dbe934a、実checker SHA256 b836af79b5246169fcdeef6e803b840bdfb883b488c3d17ce1a7744c2b49261f。旧r6/r7/r8の結果は異なる版の履歴であり現checkerの証拠として使わない。
- Stage4のr2 CUDA onedir exe SHA256 de451ad69a8f2bac122813dcf1a2f1f8c235aa899873db74845794d81e781e1f。実GUI receipt artifacts/p3-353-stage4-native-window-r2/result.json SHA256 56a65738bd277aa8ef17f8779c348c1ffcf009f9a336e091a275d34f33fd4a48 は status=passed/自然終了/port解放/最小化/復帰/確認承認/UI inspection成功。実GPU推論・本番画像操作には拡張しない。

## 4. 原Fletデータ保全と証跡秘匿

保護原root S:/Projects/PixivVault に対し、読取専用・junction/reparse非追跡の inventory を実施。**70,898エントリ走査／DB系36ファイル196,214,784 bytes／画像66,534ファイル13,572,769,196 bytes／ZIP等7ファイル22,774,189 bytes／Cookie 1ファイル1,427 bytes、列挙エラー0、打切りなし**。分類とバイト数だけを出力し、実パス名・作品ID・作者ID・画像本文・Cookie内容・Cookie SHAを会話/Gitへ出していない。

この値はStage5時点の**存在・サイズinventoryであり、試験開始以前の全66,578ファイルと比較した内容同一性を意味しない**。既存read-r3のCookie単体before/after一致と各合成試験のsource_tree不変は有効な限定証拠。**原DB・画像・ZIP・Cookie全体の前後完全hash一致は現時点未検証**。完全保証には、安全な保存済み試験前baselineか、限定した新テストを囲むメモリ内の2回内容digest照合が必要。画像/DB/ZIPの名前とsecret digestを公開証拠に記録しない。

## 5. 修正/再試験・レビュー・CIの進捗

- Stage4修正・checker・CI・docsの所有6ファイルを選択的にコミット：**fea56eb9866358c4c123d3b480927312c4bf548c**。他担当P3-348のdirtyソース/テスト/docsは含めていない。独立worker-6は最新r9と9テストをread-onlyで再監査し、当該checker限定範囲の追加確定P1/P2=0（全製品最終レビューではない）。Stage5開始前HEAD8b016673は履歴・チェックポイントで、現部分コミットHEADと区別。
- Stage5の全pytestは artifacts/p3-354-stage5-full-r1/ に既存専用Python、numpy shim、既設CPU codecで実行。**4,266 collected／4,260 PASS／6 SKIP／0 FAIL・ERROR／608 warnings／739.34秒／exit0**。XML SHA256 3b7ca874f77a61963e1732bba1393fbab209a0db44a13f0f67fe5f78017e7402。6 SKIPはWindows symlink権限によるもので、全ゲートをPASSにしない。実行開始後に追加した第9のStage4 checker回帰は別に9 PASSを回収したが、全pytest元コレクションに遡及加算しない。テスト追加・実装変更後は現候補の差分回帰が必要。
- Stage4の前回局所 focused は12 PASS、新規ランキングUI関連132 PASS、合成設定1 PASS、受入24GUI、workflow4/4、ZIP4/4と限定成功。ただしこれらは現在HEADと同じ条件での総合実機完走ではない。
- Github接続先としてのoriginは **S:/Projects/PixivVault-NiceGUI-p3-capacity-fixture-repair-20261028 のローカルGit**。originへのローカルpushをGitHub公開/PR作成成功と呼ばない。現状 .github/workflows/ci.yml のformal-152 acceptanceはmanifest未整備で意図的RED。全ゲート未達の間はmain直接push・merge禁止。
- **2026-10-31 00:00 JST追加・独立review P1是正（CI環境）**：通常 tests job は .[test,video] のみ導入するが、Stage4 GUI tests/test_stage4_geometry_guard.py がPlaywrightをmodule importしChromiumを起動するため、クリーンWindows/Ubuntu CIでcollection/runtimeを阻害するP1を独立worker-5が指摘。ブラウザー操作10ケースを tools/check_stage4_geometry_guard.py に分離し、CI browser jobのChromium導入後に明示実行。通常tests配下にはブラウザー非依存の必須tab/パーセンタイル8テストを残した。修正途中の artifacts/p3-354-stage5-ci-p1-r1 は単体1FAIL/ブラウザー1FAIL、修正後 r2 は **通常8PASS、Chromium10PASS、両exit0**。新headのクリーンhosted CIは未実行、全体GREENには換算しない。
- **透明タブ偽GREEN P2**：CSS opacity:0 のナビボタンも従来のDOM可視判定では合格になり得た。追加したChromium生成HTMLで修正前RED 1FAIL、親祖先もopacityを判定するvisibleと必須tabが実際のnav矩形に含まれることを必須とするcheckerへ修正。最新 artifacts/p3-354-stage5-geometry-r10/result.json（SHA256 5c53c5063b32b54530b1441ba80323c060e78cda9494f2746ea59aa0a83af09b）は**4画面条件/24必須tab/80warm/success=true、自然終了/port解放、source不変、外部通信0**。checker SHA256 4a419d86e6376a8652bccfbbd2cd15bfa1f51f63e293d14397df06ec70a913eb。前r9のGREENは本修正前の限定歴史であり現checkerには継承しない。
- **原Flet棚卸しの独立証拠補強**：artifacts/p3-354-stage5-integrity/inventory-receipt-r2.json（SHA256 15b4c2f8799e20dd415f3c8672016b8cf6f8d0566d494c8dffe3360cbd89e69d）に匿名カテゴリ集計・走査エラー0・非打切りを保存。機密復元可能な個別ID/パス/ハッシュは保存しない。同じ全原本の内容SHAを前後でメモリ照合する試験は artifacts/p3-354-stage5-integrity/guard-result.json の **success=false/exit1、sanitized RuntimeError** であり、内容同一性の証明には**使わない**。原因は秘匿したまま推測で断定せず、限定エラー分類で次回切分けを要する。
- 最終独立レビューは実装者以外に、固定HEAD、すべてのtracked/dirty差分・最新版のreview receipt・各P1/P2を対象に実施してもらう。今回のworker-4/6のStage4限定reviewを全製品P1/P2ゼロへ昇格させない。

## 6. 残件・推奨と明示承認が必要な選択

1. **最優先（承認なしで続行）**：152原ID/479bindingの未証明receiptと原開始条件を埋め、D16/K13等の原再現、OS race/6SKIP、Stage4検査の指摘を順にRED→GREEN→別担当再reviewで閉じる。実験は生成fixture・専用管理root・合成Cookie。
2. **実データ安全ゲート（承認済み範囲内、保護準備が先）**：既存Cookie読取の実GETを既達の5件成功として再利用し、追加実保存を始める前に実データ保存先・ZIP出力・Vault/設定rootが既設Flet原本と一切重ならないこと、外部通信制限、実アカウントPOSTなし、元データbefore/after安全証明、復旧経路をpreflightで確定。確定できなければ実通信を進めない。
3. **仕様判断（明示承認まで保留）**：U01既定をFletのfollowedへ合わせる案と、X07を安全copy既定のままユーザーへ説明する案を分離。推奨は**安全copy既定を維持**し、原版との差と空き容量への影響を説明して本人判断を得ること。Flet既定moveへの無断切替は禁止。
4. **UX比較と性能（承認済み試験は続行可能）**：隔離したFlet原版と最新NiceGUIを同じ画面幅・物理DPI・3頁・入力・操作順で30回以上測定し、p50/p95/max/画面capture overhead/private bytes、全画面・全ダイアログの失敗・改善と未検証を一画面一行で記録。旧viewerの速さを新製品へ継承しない。
5. **統合（全ゲートPASS後のみ）**：head・CI SHA一致、実装外最終独立レビュー、branch protection/承認、head CI全GREENを厳格確認してから既存Git運用に従いPR/mainへ。統合後CIと起動・リソース・原本保護を再確認。

以上の1件でも欠ければ release_ready は false。失敗・未実施・SKIP・不明をPASSへ換算しない。

## 7. 2026-10-31 追加・引継ぎ後の証拠更新（release判定は不変）

- 実worktree `phase11/p3-vault-cleanup-race-20261028`、HEAD `fea56eb9866358c4c123d3b480927312c4bf548c`、stash空。`origin` はローカルGit targetのまま。他担当P3-348のdirtyソース・テスト・`docs/PROGRESS.md` は一切stage/編集/破棄しない。
- Stage4 geometry 最新 `artifacts/p3-354-stage5-geometry-r12/result.json`（SHA256 `43f605e37e8b59884e8075afd83f6ee48ba0deba8a5b93221cd7b6214d480839`）：合成Chromiumで4画面条件、24必須tab、80warm測定、正常終了・port解放・source不変、worker-5の当該範囲限定APPROVE。物理DPI/全画面受入ではない。旧r9/r10の結果は歴史として保持。
- 全pytest `artifacts/p3-354-stage5-full-r2/result.xml`（SHA256 `73b9200dedf48e9d9dc53cfb690f27c9f62d63f213603a407e32433bb4e22ec6`）：4266 collected、4260 PASS、6 Windows symlink権限SKIP、0 FAIL/ERROR、608 warnings、668.65秒、exit0。今回追記のdirty checker全体を含む最終全試験ではなく、6 SKIPも未解消。
- 全原Flet保護範囲の限定内容guard `artifacts/p3-354-stage5-integrity/r2/guard-result.json`（SHA256 `3855f2193fe3d14c7cd998a8b352e7f1048358ecf8a250f1bac8ab108cc5a036`）：合成GUI試験を囲むDB36/画像66534/ZIP7/Cookie1の全66578ファイル前後byte同一、差異0、exit0。個別原path・Cookie値・個別秘密digestは非出力。旧r1 FAILは消さない。開発開始前baselineや他の実Cookie/全試験には外挿しない。
- K13 (`v8-K13-01`) は独立合成GUI checker `tools/check_k13_first_page_retry_browser.py` で weekly/page1の500失敗後に同条件を実fetchクリックでRetryし、旧カード保持/ボタン復帰/読取同条件2回/正常終了/port解放/source不変を確認。`artifacts/p3-355-stage5-k13-first-retry-r2/result.json` SHA256 `1fa59100dcb3a3f14c6396475f3f9a75816fafe59f62d3f183f779767748c7b5`、checker SHA256 `9f87245f51890db4d61254350fc57a475a13ba6b0d0d88df9b64a0868bc44117`。原152全件formal PASSはなお0、K13原bindingと受入receiptの正式連結・独立確認は未完。
- D16 gallery/image競合 `artifacts/p3-355-stage5-d16-browser-r1` は旧checker期待と現製品仕様の衝突でFAIL。`r2` は image_races 16チェック成功（学習404→4判定disabled→明示Retry→decode/判定復帰を含む）が gallery 画像の高さ制限時の強制拡大期待でFAIL。`r3` は gallery/learning/library各checker成功、image_racesの強化したDOM検証でFAIL。いずれも全体GREENへ格上げしない。異ページ画像は旧frameを消し、同一ページ品質変更は安全な旧frameを保持する製品契約を維持。
- 独立checkerレビューではA/B/Cの実decode注入証明、DOM node identity、同一ページ品質fallback、browser runnerの所有者/終了追跡/checker SHA、404例外範囲にP2改善がある。現行編集はローカルで再試験・再レビュー継続中。これらを全製品P1/P2ゼロと見なさない。

### D16/K13 再試験追記（現候補の限定結果）

- `artifacts/p3-355-stage5-d16-browser-r4/runner-result.json` SHA256 `d0a88bcc9ac1c84a0bb6d4c2b1d826490afdee72859295872ad7d40ef0410cba`：gallery/learning/image_races/library の4 checkerすべてexit0、runner success=true、srcソース不変、app port解放。`image_races/success.json` SHA256 `2d7ab5ec4886abe29ba1021b7ae2ebb4254e832fa9616879c90cab152ff6b764` は18チェック成功。独立worker-5はA/B/C mounted decode注入、成功candidate DOM identity、同一ページ原寸失敗時の旧front保持/回復の指摘P2について**当該checker限定APPROVE**。ただし runner のapp returncode=1はterminateによる停止であり、**自然終了とは呼ばない**。checker/runnerの所有者証明・子孫残留確認・SHA・過剰404許可にはworker-6の未解決P2がある。
- K13 `tools/check_k13_first_page_retry_browser.py` は本人専用合成サーバーlistenerのPID・親PIDと起動rootの関係、nonce付き所有エンドポイント、checkerと補助3モジュールのrun前後SHA固定を追加し、Chromium CI browser jobへ専用ステップを追加。r3は初回PID同一性判定の誤りでFAIL、r4はGUI操作3件完走したがコンソールerror1でFAIL、r5/r6は連続してsuccess=true、3チェック・同一weekly/page1 2回・owner確認・nonce正常shutdown・port解放・全source/harness不変。r6 `artifacts/p3-355-stage5-k13-first-retry-r6/result.json` SHA256 `795bb9cc488761e30388f1f7356141268f31fddc43ad13a98d8f50810d11b613`。r4の一過性console errorは原因未確定で歴史FAILを保持し、hosted CIをPASSと呼ばない。

## 8. 追加の独立レビュー対応と所有終了証明（2026-10-31 JST）

- `tools/run_browser_checks.py` と製品の `bootstrap.py` に合成データ時のみ有効なnonce付所有確認/終了エンドポイントを追加。`PV_SYNTHETIC_BROWSER_OWNER_NONCE` は呼出し毎にランダム生成し、他モードではendpointを登録しない。checker/runnerの実行前後SHA変化も検査し、証拠パスの再利用を拒否。`tests/test_browser_runner.py` はnonce/非所有PID/正常終了ACK/timeout等の否定系を追加した。`artifacts/p3-355-stage5-runner-shutdown-cpu-r3/result.xml` に22 PASS（当時版）、`artifacts/p3-355-stage5-runner-bootstrap-focused-r1/result.xml` にbootstrap+runnerの38 PASS、各exit0。後続テストの追加分は当該旧XMLに含まれない。
- owned Chromium `artifacts/p3-355-stage5-d16-browser-r7/runner-result.json` SHA256 `00f5103f03911880eaf30f659016611693070269450da579e780be4a368b49af`、r8 SHA256 `364ff98843879609cc767400b69324a94ecfc2f5a820fe5c9a64dc747929f40f`：双方gallery/learning/image_races/libraryの4 checker exit0、親・listener PIDとnonce付経路一致、合成アプリ正常shutdown exit0、port解放、forced_cleanup=false、src/harness変化なし。これは限定合成GUIとroot正常終了の証明であり、孫プロセス全消滅の証明ではない。
- D16新たなHTTP404の厳格なresponse-path/console対応を加えたr9では、正確な2件注入の確認へ到達する前に**同一ページ品質fallbackの候補昇格確認タイミング**で1 checker FAIL。`artifacts/p3-355-stage5-d16-browser-r9/runner-result.json`、`image_races/success.json` をFAILのまま保存。製品本体の失敗かcheckerの待機不足かは切り分け対象。現checkは「既存の成功画像で満たされる画面表示」を新候補decode完了と取り違えないよう、DOM identityが昇格するまで明示waitする変更。新r10は別証跡で実受入するまでPASSにしない。
- 独立worker-5はowner routeの非認証公開P1を確認していない一方、**checker/アプリの子・孫プロセスのPID+作成時刻と終了完全性未証明**、およびowner/normal shutdownの偽GREENを避ける否定系テストが不足するP2を指摘。後者は追加CPUケースの部分修正を進めたが、前者の全プロセスzeroとLinux hosted CIは未完。合成r7/r8のroot正常終了だけでStage5 P1/P2ゼロを宣言しない。
- Flet隔離比較r1の90秒window ownership失敗は、保存原票がeligible top HWNDの件数(0/複数)を区別していない。原Fletデータでの再実行は禁止したまま、`artifacts/p3-344-performance/manage.py` に所有PID/birth固定の匿名window census（class/visible/GW_OWNER/候補数）を追加、AST検証済。まだ新しい起動/画素比較の証拠ではない。r1失敗を原因確定/正常終了と誤認しない。

**Release remains rejected**: 正式152/479=0 formal PASS、6 symlink SKIP、実Cookie専用root/Vault未証明、Flet実画面/性能・物理DPI、全独立レビュー、hosted CI/PRは未達。`main` 統合・完成認定禁止。

## 9. Stage4 Flet/native同一合成作品・同一物理client比較（2026-10-31 JST）

- 隔離Flet r1の `unique owned top window unavailable` は根拠を残し、r2では一意HWND検出まで進んだが1920×1080物理clientへのresizeでFAIL。r2は既設の3択不変条件（size/DPI/monitor）どれが破れたか単独では断定不能。PID/birth所有の通常WM_CLOSEで自然exit0、残留0、固定fixture不変。
- `artifacts/p3-344-performance/p3-355-stage5-flet-r3-probe/exit.json` SHA256 `5d53ffc804e9ad1bb6cc1a0b58fa9b905a11911a24c994586977151e9074f221`：起動直後client1264×681、DPI96、モニター1920×1080、作業領域のbottom=1032。`r4-settled-probe/exit.json` SHA256 `1caee1527c723370f10f2ae81dc148f4280f44d0c04b1400862e03c1d7977451`：Fletタイトル/寸法安定後のclient1904×1041、outer1920×1080。1920×1080物理client指定は同モニターの作業領域に物理的に収まらない。両probeは移動/resizeなし、WM_CLOSE自然exit0、原fixture不変。
- `artifacts/p3-344-performance/p3-355-stage5-flet-r5-1800x960/exit.json` SHA256 `5d41d2bd1687a862cebd8a6ca238b861e8b4f21cd6cc2072d6443e3ccc22428f`：work area事前収まりを確認して本人専用HWNDのみ1800×960 client/DPI96、client origin40,60へ変更。実クリックで合成作品0003を開きページ1/3→2/3→3/3の各画面をscreen-GDI保存。原版の画面を目視確認、全3ページの色グリッドが固定合成PNGと対応。正常WM_CLOSE、exit0、owned child living[]、forced=false、fixture320 PNG+コピーsource不変。原本全期間の内容hash一致とは解釈しない。
- Stage4既存CUDA onedir EXE SHA256 `de451ad69a8f2bac122813dcf1a2f1f8c235aa899873db74845794d81e781e1f` を独立専用profile/native rootで同じ1800×960/DPI96起動。`artifacts/p3-344-performance/p3-355-stage5-native-r1-1800x960/native-viewer.json` SHA256 `448961f60844d21b866b4fc45fa7cbed3af2d47b386b7320b8e6ba266be278d` に固定320画像すべてのHTTP bytes/SHA一致、ページ1/3→2/3→3/3実WebView操作と2頁目以降の正しいURL/PNG decodeを記録。物理screen-GDI captureは `screen client occluded or PID identity unknown` として拒否されたため、所有root PID/birthとHTTP/CDP listenerを再照合後、WebView CDPで同3ページをPNG保存。`native-dom-pages.json` SHA256 `881dbcebf50d1d7a961864f573e2a9a5721973a97744f1d7d04aad9a661bf654`。`exit.json` SHA256 `f0b86d366f78c4b4f09d2aa489d7800b07b2561ccef85627dfaa00ed6ffa5caf` はnative確認ダイアログ経由正常exit0、owned living[]、forced=false、fixture不変・port解放。
- ピクセル範囲比較 `artifacts/p3-344-performance/p3-355-stage5-matched-image-area-r2.json` SHA256 `79622141b879357e2c7ded971986c210c8f833b79d53f383ecd4bf46e52ffa34` は3頁で同一：Flet画像bbox `[396,202,1405,875]` → 1009×673、NiceGUI bbox `[453,270,1347,866]` → 894×596、幅・高さ比約0.886、表示面積約78.5%。画像は外周のカラータイル画素から計測。初回r1比較は別UI部分の似た色を拾う偽範囲が混入し、r2で各軸100画素以上の同色行列と元画像アスペクト比の検証を追加して**r1判定を破棄せず歴史的誤判定**とした。
- この差は原版よりNiceGUIの主画像が約11.4%小さく表示されるという具体的UX差。実データの作品破損や保存結果の差は証明していない。CDPとscreen-GDIのcaptureモードは異なる。現在ソースを再freezeしたexeではないため **S5-11/S5-12をUNVERIFIEDのまま維持**。変更するなら画面設計・操作範囲への影響を精査し、仕様承認境界を守る。物理遮蔽を無断で解消するために他アプリのwindowを移動・閉じることはしない。
- 比較証拠の独立レビューで、r2 reportの `source_fixture_sha256` が固定fixture manifestではなく単独page2 PNG SHAを指すP2記載誤解が見つかった。r2の数値を消さず、`artifacts/p3-344-performance/p3-355-stage5-matched-image-area-r3.json` SHA256 `b8d687fca4215442c43c65fd32f1e1c58b673f3e95a7932563bdac2c52f0b651` に検証可能な正式区別として `fixture_manifest_sha256=f1406aa85562b6b1b477d71d236bf175433dcb35d70796f1957018b4f56b3524` と `source_page2_sha256=1748d62b0b2a2e79aec0a1e2720b59ee437100cafc6723b1a62fbaf249c4740a` を保存。Flet/Native owner manifest同一、PNG6枚の撮影証跡SHAを全数照合。3頁のbbox/0.886の差はr2と同一。現行比較の正本はr3、r1/r2は履歴として保持する。

## 10. 正式IDの限定証拠最新版と未完了P2（2026-10-31 JST）

- `docs/P3_355_D16_K13_SCOPED_EVIDENCE_2026-10-31.json` は逐語の原v8期待と限定synthetic観測を分け、正式 `formal_pass_added=0` を維持する。D16 `v8-D16-01` 最新候補は `artifacts/p3-355-stage5-d16-browser-r14/runner-result.json` SHA256 `adcf66a19628f145384488f90518b5266ec9d4cdd510c2e42634ffa2f91f5725`、4/4 GUI checker exit0、うちimage-racesの `success.json` SHA256 `7e4b532e7188588cdaed7e0ac564053cc186d72b5d8e0f1ba16c8940379aa2ef` で19チェック・学習404注入→4判定disabled→明示Retry→decode/判定復帰を観測。合成runnerはnonce所有確認・正常exit0・port解放・観測済み子孫0・source/harness不変。原Fletの正規開始条件/全binding/物理DPI/独立最終reviewは未完。
- K13 `v8-K13-01` は `artifacts/p3-355-stage5-k13-first-retry-r9/result.json` SHA256 `21c11c997268f52a5a0149fab7ad3d9609d20c3cc322f8db4e41c27174cecfdd`、checker SHA256 `1faae7c4001aa0cd5ccdc0462eb5574424e2d81754588662532eba28aac40bf2`。daily第1頁成功→weekly第1頁1回だけ500→旧3カード保持/追加無効→同条件fetchを画面クリックで再取得成功（weekly/page1リーダー引数完全一致2回）。正常exit0・owner観測3/残留0・port解放、強制終了0、外部ソケット試行拒否数0・source/harness不変。更新を含む全操作部品の原K-1受入は未完。K13従前r3/r4のFAILやr7/r8成功は別時点として保存。
- runner `tools/owned_process_tree.py` の生存・PID/birth観測は観測済み子孫の残留ゼロを証明する。150msスナップショットの間で短命中間親が起動・終了し、その孫だけ残ったケースまで完全証明するものではない（独立worker-7 P2）。K13の異常時停止にも最小の所有・時間制限ガードを追加したが、Linux/macOS/Windows全環境の実故障注入やCI hostedは未実施。**全製品P1/P2ゼロとは判定しない**。
- 最新のfocused CPU回帰 `artifacts/p3-355-stage5-final-focused-r1/result.xml` は `tests/test_bootstrap.py`、`tests/test_browser_runner.py`、`tests/test_owned_process_tree.py`、`tests/test_stage4_geometry_guard.py` の**59 PASS / exit0**。full-r2 の **4260 PASS/6 Windows symlink SKIP** は依然最新dirty checker全体を再収集したものではない。`docs/P3_354_STAGE5_RELEASE_GATES_2026-10-30.json` は **16個：4 FAIL、12 UNVERIFIED、0 PASS、release_ready=false**、正式原152/479は0 formal PASSのまま。
- K13の独立worker-7から、異常終了が一度発生しても最終rootが0であればGREENとなるP2の可能性を受領。`tools/check_k13_first_page_retry_browser.py` の `scoped_success` を分離し、shutdown/cleanup timeout、owner未検証、強制終了等の履歴フラグを一つでも含む場合に成功を拒否する形式とした。生成CPU `tests/test_k13_browser_guard.py` で異常履歴・欠損/残留を明示否定。関連 `artifacts/p3-355-stage5-k13-guard-focused-r1/result.xml` は **53 PASS/exit0**。新規 owned Chromium `artifacts/p3-355-stage5-k13-first-retry-r10/result.json` SHA256 `3ae82aa07265fee18c8619a3238408c2f3bf83758c862e0381c7def7bef41d33`、checker SHA256 `ccf314a5baf91594ac081b518b01b9f5e52f748ed9e577a729dd81044a2acf3f` は3/3限定操作・正常終了・観測済み3 PID残留0、forced=false、source/harness不変で再度GREEN。r9までの履歴を上書きせず、新r10を現K13限定正本とする。ただし全原K-1と正式152ゲートは依然未完。
