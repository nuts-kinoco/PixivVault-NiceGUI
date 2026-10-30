# P3-353 Stage 4 — Windows実GUI・UI/UX・性能の厳格受入（2026-10-30 JST）

## 判定と保護した対象

- **全Stage4: 未完了 / 正式全画面・Flet同条件性能・初見実Cookieフローは未受入。** この文書内のPASSは各専用生成fixture/Windows実機の限定scopeのみ。Stage3の形式152は **0 PASS / 152 INCOMPLETE**、実Cookieは以前のGET5件のみで、実DL・ZIP・Vault保存は未完。Stage2のWindows symlink権限に起因する6 SKIPも未解決。
- 開始HEAD `8b016673795fe55e2319a6a7f5cc324ebf1c6556`、作業branch `phase11/p3-vault-cleanup-race-20261028`、原Flet固定HEAD `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。試験前後で作業対象のソースハッシュを比較。他担当の未コミットP3-348ソース/テストと `docs/PROGRESS.md` は編集・stage・破棄せず保護。元FletのDB・実画像・Cookie・ZIP・設定には書き込んでいない。実Pixiv GET/POSTの新たな送信はない。
- 証拠スクリーンショットは**架空画像と架空IDだけの独立テスト画面**を `artifacts/`（Git無視）に保存。実Cookie、実作者/作品ID、pixiv実レスポンス・画像、利用者の実画面情報は追跡しない。

## Windows CUDA onedir 実GUI

| 検証対象 | 実操作・観測 | 判定 |
|---|---|---|
| Stage2コピー版（前候補） | Desktopコンピューター操作で `artifacts/p3-351-stage2-native-copy-r1/PixivVault-NiceGUI/PixivVault-NiceGUI.exe` を起動。初期のオフライン/架空作品、Cookieと保存先が未設定と表示。設定ダイアログでCookie・保存先の選択、認証確認disabled、次回起動に反映する説明を視認。ライブラリ4作者・ランキング50件の架空カードをクリックで表示 | 実機で表示・主要クリック確認、初心者の実Cookie設定完走ではない |
| Stage2コピー版・物理画面 | Desktop画面と手動生成 `artifacts/p3-353-stage4-native-manual-r1/library-1280.png`。Win32 GetWindowRect `[968,-1087,2902,-41]`、Window DPI=168（175%）。multi-monitor上のウィンドウ、スクリーンショットは架空画像のみ。各カード操作可否や全見切れの網羅を意味しない | 現物矩形観測のみ |
| Stage2コピー版終了 | 実ウィンドウへAlt+F4→OSの確認「Do you really want to quit?」→通常承認。対象exe実PID残留0 | 自然終了PASS |
| **新Stage4ソースfreeze** | `tools/freeze_windows_candidate.py --output artifacts/p3-353-stage4-native-freeze-r1`。177ファイル verified=true、source_tree_sha256 `e6162b3eca910feabf2a9799e1a6546b05d28a3adfc05f975f4d54cbec536557` | freeze PASS |
| **新CUDA onedirビルド** | freeze配下の `tools/build_windows.ps1`、既存CUDA venv、`-InferenceBackend cuda`、専用 `artifacts/p3-353-stage4-native-build-r1` へ非上書きbuild。exe SHA256 `4bb62d9450ed390232e4543aaf3484b6b77fe392e49cf1112a27d85f973ddb7e`。旧配布exeは変更なし | build exit0 |
| **新Stage4 CUDA exe受入** | `tools/check_native_window.py --inspect-ui --acknowledge-close-confirmation --no-force-cleanup --timeout 180 --exe <上記新exe> --output artifacts/p3-353-stage4-native-window-r1/result.json` → **status=passed/exit0**、health/window/UI/minimize/restore/WM_CLOSE/終了確認/normal_exit/port_released=true、UI inspection passed、errors=[]、forced_cleanup=[] | 新実機の限定nativeライフサイクルPASS |
| 新Stage4 exe再手動起動 | Desktopコンピューター操作で新exe実ウィンドウ表示を目視。架空作品ランキングデモのカードを確認。Alt+F4→OSの終了確認→Returnで自然終了。最終照合で新exe PID残留0。 | 表示・終了PASS（UI文言修正をnativeの実ランキングGET画面で確認したものではない） |

上記の配布exeは新ソースの凍結後にビルドしたが、GPU embeddingの実計算・URI・trayの全操作・実Cookieや初見ユーザーの保存操作は行っていない。実nativeを起動しただけで製品完成とはしない。

## 主要6画面の合成実ブラウザー検査（r6時点の履歴・最新r8は追記）

- 新しい専用check `tools/check_stage4_visual_geometry_browser.py` は実製品shellに合成GET/保存用readerを接続した**loopbackだけ**のサーバーを所有し、Playwright Chromiumで各タブを実クリックする。Cookieはfixture内の偽Cookieのみ。対象：ダウンロード、キュー、ライブラリ、ランキング、学習、失敗リスト。
- `artifacts/p3-353-stage4-geometry-r6/result.json` SHA256 `cc1653794cdf4f4bec5496688c831a588799ee6bb6360e3c6b7d4d57c4baf75a`。checker SHA256 `942fb1f3f1f3fc88aaf1f33c9e153cc07f8daa98c62daf77e2467b271c23bdd1` は証拠内と現物で一致。**4条件 × 6タブ=24件、全必須タブ非SKIP、24匿名スクリーンショット、80回ウォーム切替、success=true/exit0**。全source before/after一致、ページ/console/外部要求0、自然終了と所有port解放true、強制cleanupなし。
- 検査: viewport/scrollWidthによる画面幅はみ出し、ナビ同士の矩形交差、要素中心 `elementFromPoint` による遮蔽、操作ボタンの領域外・隠れたスクロール親・部分見切れをfail closed判定。結果は検査できた範囲で**横あふれ0・ナビ交差0・遮蔽0・到達不能0・必須tab SKIP0**。すべての機能やダイアログの入力/長時間処理を網羅したものではない。

| ブラウザーviewport | 再現したDPR（エミュレート） | library切替 n | p50 | p95 | max |
| --- | --- | ---: | ---: | ---: | ---: |
| 1024×768 | 1.00 | 20 | 69.95 ms | 83.21 ms | 91.09 ms |
| 1280×720 | 1.25 | 20 | 69.15 ms | 84.00 ms | 84.12 ms |
| 1800×960 | 1.00 | 20 | 68.56 ms | 91.00 ms | 138.16 ms |
| 1920×1080 | 1.75 | 20 | 75.09 ms | 86.15 ms | 86.76 ms |

**測定限界**：これはPlaywright click→`aria-current=page`成立までを対象にした計測。物理画面paint/バックエンド実画像GET/動画decode/スクリーンキャプチャのオーバーヘッドは含めておらず、Fletと同条件のp50/p95/maxにも換算しない。DPRはPlaywrightによる**エミュレーションであって実Windowsの表示倍率操作ではない**。実機DPI168は上記別のnative観測であり混同しない。

### 独立レビュー指摘によるcheckの偽陽性修正

1. **P2（checker）**：旧r1は必須タブが見つからない/disabledでもSKIPで成功し、透明fixed overlayで押せないボタンも合格と誤認。追加恒久試験 `tests/test_stage4_geometry_guard.py` のoverlay/mandatoryタブが**修正前RED 2FAIL**。クリック中心の可視要素遮蔽検査と必須6tab非SKIP検査を追加、**GREEN 2PASS**。
2. **P2（checker）**：画面端から半分見切れたボタン（例: x1000+幅100/viewport幅1024）は中心が画面外のため遮蔽判定を素通りしていた。第三の合成HTML再現を追加し**修正前RED 1FAIL／他2PASS**、部分clipと`overflow:hidden/clip`祖先の幾何判定を追加して**GREEN 3PASS**。最新r6で改めて4条件24画面を実走し0件を確認。

これらは製品ソース自体のP1/P2を意味せず、受入checkの不適切なPASSを防ぐための修正。独立最終レビューの判定は別管理。

## 設定→取得→閲覧→整理→書出し（確認した条件と不足）

- **実機の設定入口**：Desktopで初期表示→設定dialog→Cookie file選択と専用保存先選択の場所・再起動反映案内・Cookie未設定時の認証確認disabledを視認。実Cookieを選択・認証・保存していない。Cookie秘密値は表示・外部出力していない。
- **合成Native Profile操作（追加）**：`PIXIVVAULT_BROWSER_CHANNEL=chromium`、`python -X utf8 -m pytest -q tools/check_native_profile_browser.py --basetemp=artifacts/p3-353-stage4-profile-r1/tmp --junitxml=artifacts/p3-353-stage4-profile-r1/result.xml` → **1PASS/exit0/13.38秒**。架空Cookieと架空保存親rootの設定ダイアログ・inline設定で選択/取消/再選択/保存、偽認証確認コール、再起動後の値保持、旧読取専用DB参照が失われた場合の選び直し/取消が実ブラウザーで通った。元Cookieの認証/本番保存先を使ったのではない。
- **合成ブラウザーでの取得→閲覧→復帰**：`tools/check_managed_downloads_browser.py --channel chromium --output artifacts/p3-353-stage4-workflow-r1`、`result.json` SHA256 `a10fa95b87704dd3e15abd7c79d21e955a3b0d682aa1e23071bb2e27dbc08368`。架空Cookie、ブックマーク更新、synthetic GET失敗注入→既存リスト保持→明示再取得回復、検索・選択→ジョブ投入→キュー完了→ライブラリ閲覧と2ページ原寸byte/pixel確認、フォーカス復帰/再起動を画面操作。1280×720・1920×1080の初回/再起動**4/4 PASS、正常終了4/4、port解放4/4、page/console/外部通信エラー0**。
- **合成整理・ZIP**：Stage3保存済み `artifacts/p3-318-x07-quarantine/p3-352-stage3-x07-move-r4/result.json` で物理move・隔離ファイルと元実体の消失→復元SHA一致を確認。Stage3 `artifacts/p3-352-stage3-legacy-zip-r1/result.json` で生成作者ZIPの2ページの名前・CRC/DEFLATED・元bytes一致と再起動後非上書き4/4 PASS。**今回の単一UIセッションで先頭から末尾まで実行した証拠ではない**。
- 初見の第三者による**無説明操作**、実Cookie→実差分DL→実画像閲覧→整理→実ZIP→専用Vaultの完全一気通貫、native内での同一連続フローは**未実施**。Synthetic個別成功の合算を初見全体PASSとしない。

## 実画面で見つけたP2表示不具合と修正（仕様変更なし）

| 対象 | 修正前の具体的問題 | 変更 | 利用者への効果と証拠 |
| --- | --- | --- | --- |
| `ui/ranking_view.py` 初回未取得 | `条件を選び、取得してください。` と `該当する作品がありません` が同時表示。未取得を**取得済み0件**と誤解させる | 初期の空欄を `ランキングは未取得です。条件を選び「取得・更新」を押してください。` とし、実取得後の空欄は `取得した条件に該当する作品がありません。` に区別。GET/データ/操作フローは変更せずラベルのみ | `tests/test_stage4_ranking_empty_ux.py` で修正前**1FAIL**、修正後の関連UI/failure/geometry回帰で **12PASS/exit0**（`artifacts/p3-353-stage4-focused-regression-r1/result.xml`）。変更を含む合成画面 `artifacts/p3-353-stage4-geometry-r6/1800x960-dpr1_0-ranking.png` を実視認。改修後のCUDA exeビルドと基本実native起動もPASS |

追加の関連ランキング8ファイル＋Stage4修正2ファイルを `python -X utf8 -m pytest -q --junitxml=artifacts/p3-353-stage4-ranking-all-r1/result.xml ...` で実行し **132 PASS/0 FAIL/exit0（5.01秒）**。全pytest再実行・CI本番成功へは拡張せず、関連回帰として保持。

新機能の採否、既定設定、操作導線の仕様変更は勝手に適用していない。Stage3のU01（Flet既定followed/NiceGUI bookmark）とX07（Flet確認後既定物理move/NiceGUI安全側copy+明示move）は**承認対象の仕様差**として未変更。

## Fletとの比較（劣化/優位の断定禁止）

- 保存済み生成Flet画面 `artifacts/p3-344-performance/flet-matched-r2/gallery-settled.png` とNiceGUI合成GUIのランキング画面を目視したが、Fletは取得済み画像、NiceGUIは未取得の初回であり、**入力条件が異なるのでカード件数・速度・UXの優劣は未判定**。
- `artifacts/p3-344-performance/matched-image-boxes-r2.json` にある固定の幾何比較は物理client1800×960、DPI96、Flet画像領域894×597 / NiceGUI旧候補894×596（1pixel差）。この記録は**現Stage4最新CUDA exeの時間・完全一致の証明ではない**。
- 過去の別条件限定viewer（2026-10-22、DPI168）ではFlet p50=231.760/p95=250.330ms、NiceGUI p50=115.096/p95=122.386ms。ただしNiceGUI private bytesは539.828 MiB、Flet408.199 MiB（約+32.2%）。**以前の隔離viewer・別sourceであり今回の完成製品へ継承しない**。現HEADのp50/p95/max/capture overhead/private bytesとFlet同一対象三頁の反復校正は未測定。
- 次の正式性能比較は `docs/NATIVE_BENCHMARK_METHOD.md` の同じ専用Flet/新native所有PID/HWND・物理client/DPI・画像三頁・同じ順序・capture-mode校正・各30以上の反復とprivate bytes計測を条件にする。元Fletを本番Cookie/DBで起動してこの証明を行うことは禁止。

| 画面単位 | Flet原版の照合状況 | NiceGUI今回の実操作・改善・不足 |
| --- | --- | --- |
| ランキング | 旧固定合成画像の `flet-matched-r2/gallery-settled.png` に日/週/月・作品種別・R-18/AI・タグ・並び順・ランキンググリッドの実表示。旧データ取得済み | 今回は4画面条件でメニューボタン/検索/カード表示場所、初回未取得表示、必要なクリック到達を確認。**未取得を0件と誤称するP2**を改善。Fletと同条件の取得済み件数やタイミングは未比較 |
| ダウンロード・キュー | Flet原操作台帳の条件はStage3と同じ正本。今回の実Flet画面操作はなし | 合成Pixivリーダーで失敗→明示再取得→検索選択→キュー完了・再起動を確認。ただしU01既定mode差は残存、実Cookie/native全工程は未受入 |
| ライブラリ・閲覧 | 旧Flet固定viewer画像領域の幾何894×597（条件はDPI96・1800×960） | 新nativeで架空作者カード表示、合成browserで2ページのviewer pixelとフォーカス復帰を確認。現在版のFlet同一画面/画像と全3ページの比較は未実施 |
| 学習 | 原D01再取得機能は独立ソース監査済み。今回のFlet実操作はなし | 4画面条件で架空画像・4判定ボタンの見切れ確認。Stage3 D01の合成retry限定成功は別証拠、20round自然復帰/新実機では未証明 |
| 失敗リスト・重複整理 | Fletの詳細対照は元台帳の範囲だけ、今回のFlet実GUIは未実施 | 失敗リストは今回ブラウザーで画面切替と幾何まで。X07重複物理moveと復元はStage3合成GUI限定PASSだが、Stage4新native同一ユーザーフローは未実施 |

新バージョンの優位性として判定できるのは**今回の曖昧な未取得案内の修正そのもの**だけ。機能数・操作数・速度・メモリをFletより優れると結論づける比較条件は揃っていない。

## 残る受入・独立最終判定

- **Stage5開始時のStage4追加監査**：独立worker-6が、実ランキングのmode/content/dateラベル省略（P2 UI）とinput透明overlay・縦スクロール祖先がある横方向見切れを偽GREENにするchecker P2 2件を指摘。現物の src/pixivvault_nicegui/ui/ranking_view.py:46-53 に最小幅145px/165px/270pxを追加。取得・保存・既定操作フローは変更しない。tests/test_stage4_geometry_guard.py の遮蔽と方向別見切れ回帰を含む **8 PASS**。
- **表示破綻の修正前RED**：artifacts/p3-353-stage4-geometry-r7/result.json（SHA256 63badd56ac8020f6d71e3955e7a3ad611419cc78827109cf10cc6e13533ef205）はランキングのclippedTextLabelsを3件検出しsuccess=false/exit1。FAILを消さず保持。
- **修正後r8 GREEN（最新該当限定証拠）**：artifacts/p3-353-stage4-geometry-r8/result.json（SHA256 a5674f5cca3860c14d684d36fb6a6c1f8d8e59fa274a84c5d40a5c7e44b4bce4）、checker SHA256 e3671c79983f1a5ef36967440256906b54c16a9fa27a3df28bbde440aaed40f4 と現物一致。4条件×必須6タブ、24架空PNG、80ウォーム遷移、success=true/exit0、source_changed=false、normal_exit/port_released=true、外部通信/ブラウザー例外0。幅別各20回の p50/p95/max：1024×768 DPR1.0=71.84/84.05/84.42ms、1280×720 DPR1.25=78.48/88.65/98.63ms、1800×960 DPR1.0=68.19/87.32/88.54ms、1920×1080 DPR1.75=78.50/93.18/94.08ms。Flet同条件の物理paint性能には換算しない。手動視認でもr8/1024x768-dpr1_0-ranking.pngの3ラベルは省略が解消。
- **最終Native再ビルド（旧r1とは別exe）**：artifacts/p3-353-stage4-native-freeze-r2/source-manifest.json から artifacts/p3-353-stage4-native-build-r2/dist/PixivVault-NiceGUI/PixivVault-NiceGUI.exe を新規生成、exe SHA256 de451ad69a8f2bac122813dcf1a2f1f8c235aa899873db74845794d81e781e1f。artifacts/p3-353-stage4-native-window-r2/result.json SHA256 56a65738bd277aa8ef17f8779c348c1ffcf009f9a336e091a275d34f33fd4a48 はstatus=passed/exit0、UI inspection、最小化、復帰、終了確認、natural exit、port解放が全true、強制終了0・残留PID0。GPU実計算・実Cookie・Flet原操作の合格には拡張しない。
- **Stage5で発見された追加checker P2修復**：同じQuasar q-field内に透明な阻害用divが重なっても以前はsameField=trueとして無条件免除されていた。tests/test_stage4_geometry_guard.py の生成HTML例で修正前RED 1FAILを回収。checkerをQuasar標準装飾/関連labelだけの免除に絞り、全9ケースGREEN。最新 artifacts/p3-354-stage5-geometry-r9/result.json SHA256 3fc526816daf39cd84787b62c8de42b0b8c04e988e73997d897d72897dbe934a、checker SHA256 b836af79b5246169fcdeef6e803b840bdfb883b488c3d17ce1a7744c2b49261f。4 viewport/24必須タブ/80ウォーム遷移、遮蔽/見切れ/ラベルclip/必須tab SKIP 0、normal_exit・port_released=true、source_changed=false。r8はこのP2修復前の履歴であり、r9を最新限定ブラウザー受入に用いる。独立レビューは別途回収。
- **2026-10-31 00:00 JST修正・最新はr10**：独立Stage5最終監査から通常CIジョブでPlaywright未導入なのに実ブラウザー9テストが収集されるP1を発見。tests側へ通常pytestのみ8ケースを残し、実Chromium10ケースは tools/check_stage4_geometry_guard.py に移してbrowser jobへ登録。未視認opacity0タブを検出するchecker P2も修正前RED→修正後GREEN。限定検査最新は artifacts/p3-354-stage5-geometry-r10/result.json SHA256 5c53c5063b32b54530b1441ba80323c060e78cda9494f2746ea59aa0a83af09b、4幅×6タブ24画面・80遷移、正常終了/port解放・source不変、実HTTPなしでPASS。r9はこのP2修正前履歴。CI本番やFlet同条件比較にPASS継承しない。
- これらは各限定受入であり、独立worker-6による修正後再判定を別途回収するまでStage4総合P1/P2ゼロとは扱わない。
- 実Windowsで1280×720以外のウィンドウサイズ変更、実Windows表示倍率100/125/175%を変更して全ダイアログ・全エラー画面の目視/可操作性を検証、フリッカー・高DPI再起動/OS tray・URI、GPU実推論、長時間処理中の描画応答・中断/復帰の全条件は未網羅。
- 実Cookieの本人承認済み限定作者差分DLについても専用管理root/ZIP/Vault隔離・復旧前flightが未完。元Fletデータへ書込みを避けてから実施する。外部HTTP429は合成障害のみ既受入。
- 形式152操作479参照の全GUI個別receipt、Flet同条件p50/p95/max/capture overhead/private bytes、GitHub PR/headと全CI、最終包括P1/P2ゼロは未達。**Stage4全体GREEN・製品完成・main統合は禁止。**

## 2026-10-31 追加 — 固定合成作品のFlet/native実画面比較（当時「最新r10」記述を更新）

- 幾何checkerの現行限定成功は `artifacts/p3-354-stage5-geometry-r12/result.json`（4条件/24必須tab/80 warm/exit0、独立worker-5限定APPROVE）。後続のD16 grouped Chromium `artifacts/p3-355-stage5-d16-browser-r14/runner-result.json` ではgallery/learning/image-races/libraryが4/4 exit0、owned launcherと各checkerの**観測済み**プロセス残留0、root正常終了、強制停止なし、source/harness SHA不変。ただし短命中間プロセスをスナップショットで見逃す可能性とCI hostedの未実行は残り、全機能PASSではない。
- Flet隔離原ソースコピー＋固定120作品/320PNGを使い、ウィンドウのタイトル/寸法安定待ちとclient work-area事前収まりを追加。初回1920×1080 clientへのresizeは画面work-area上不可能だったため、所有PID/HWNDにだけ1800×960/DPI96 clientを設定。`artifacts/p3-344-performance/p3-355-stage5-flet-r5-1800x960/exit.json`：作品0003の3頁を実クリック、物理screen-GDI 3画像を保存、自然終了exit0、全観測PID解放、元fixture不変。
- Stage4 onedir EXE SHA256 `de451ad69a8f2bac122813dcf1a2f1f8c235aa899873db74845794d81e781e1f` を新専用profileで1800×960/DPI96で起動。合成320 PNGの実配信byte SHAが元fixtureと一致。所有CDP経路から同じ作品のページ3→2→1を押し戻して3頁表示/画像decode/PNGを保存。物理screen-GDI取得は画面遮蔽/所有点不明のため拒否（別方式のCDP画像を物理GDI成功へ読み替えない）。Nativeも通常終了exit0、owned PID残留0、port解放、元fixture不変。
- `artifacts/p3-344-performance/p3-355-stage5-matched-image-area-r3.json` SHA256 `b8d687fca4215442c43c65fd32f1e1c58b673f3e95a7932563bdac2c52f0b651`：合成5×3タイルの色・画像比率/全6スクリーンショットSHAを検証。Flet 1009×673px 対 NiceGUI 894×596px、3頁すべて同一。NiceGUIの表示幅/高さは原版比約88.6%、**面積約78.5%（21.5%減）**。NiceGUIに共通ヘッダー/タブが表示されるレイアウト差は見られるが、画像面積差との因果は未分離。これは具体的UX劣化候補。設計/操作仕様を変える全面overlay化等は未承認なので、証拠をもとに方針を整理するまで製品UIを無断変更しない。
- 旧比較r1はカラーが偶然合う他のUI画素を拾い誤った画像範囲を記録したため不採用。r2ではbboxを厳格化して上記差分を確認したが、full fixture manifestと単一PNGのSHA名が紛らわしいP2記載問題が独立レビューで見つかった。r3で `fixture_manifest_sha256` と `source_page2_sha256` を別々に検証し、6撮影PNGのSHAまで所有receiptと再照合した。r1/r2は失敗/歴史を消さず保持。`docs/P3_354_STAGE5_INTEGRATED_AUDIT_2026-10-30.md` 第9節が詳報。
- 全性能比較は**未達**。同じ物理capture方式の校正、画像面積一致、各30回以上の実GUI遷移、capture overhead/private bytes、現在HEADから再freezeしたexeが必要。原DB/実画像/Cookie/ZIP全量の試験前後bytes不変を今回の合成fixture一致に誤換算せず、Stage4/Stage5リリースゲートは引き続き不合格。
