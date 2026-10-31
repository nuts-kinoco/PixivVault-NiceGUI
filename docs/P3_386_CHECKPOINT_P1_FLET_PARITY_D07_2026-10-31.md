# P3-386 — checkpoint/P1・P2、元Flet全8タブfail-closed照合とD07実GUI

2026-10-31 JST。今回の所有対象は新しい照合器・D07 GUI検査器・両テスト・本書だけ。原Flet、元DB/画像/ZIP/Cookie/設定、実アカウント、他担当未コミット差分は変更しない。push・main統合なし。

## 1. 引継ぎと実Git状態

開始HEAD de3618f6a6575173d89c66c0bd6e3bc954f0fcff、branch phase11/p3-vault-cleanup-race-20261028。指定checkpoint 06a03de0a841fb3dc9f45782e9d1b4f1e87b2143 は merge-baseでHEADの祖先と実確認。同一HEADではない。staged0 / stash0 / 関連PID0を確認。AGENTS.md全44行、docs/CONTINUE_FROM_CHECKPOINT_2026-10-30.md全35行を全文読了。後者に残る旧P1/P2はP3-348で後続修正されたが他担当dirtyであり、チェックポイント文書の旧状態を現HEAD完成と読み替えない。

originは S:/Projects/PixivVault-NiceGUI-p3-capacity-fixture-repair-20261028 というローカルGitでありGitHubではない。実元Flet gui.py SHA256=731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939。固定152/479台帳SHA256=487065A9A52228266D540E36D3E3B83EA34A38BDDE4D1FDB54E275D725E55BB4。

## 2. 先行P1/P2の所有境界と独立監査

P3-347には物理移動直前のsource swapで唯一の正常backupを失うP1、作者設定revision変更後の旧preview受付P2、親モード切替後のhidden submit P2が記載された。後続P3-348はWindows pin handleの削除処理、pinned正常backup、OS root writer fence、作者revisionと親mode/epoch照合を別担当のdirtyへ実装。保存済みRED: source race 1FAIL、multi-writer 1FAIL、implicit author 1FAIL。修正後: root fence3PASS、作者関連65PASS、隔離管理関連62PASS。worker-1/2限定独立再レビューAPPROVE記録あり。今回worker-7による現行dirtyのread-only再監査でも対象範囲に新しい確定P1/P2なし。実POSIX、全製品、native配布と実CookieのP1/P2独立最終合格は未了。

別担当によるU再起動状態分析ではP3-348 r2の追加author-projection 2件は早すぎる初回snapshotの後に生成された正規予定jobと判明。保存済みr3（artifacts/p3-348-u-verify-20261030-r3/result.json）は1280/1920×初回/再起動4経路成功と報告。r2 novel projection ERRORの厳密な根本原因は未確定。重複GUI再実行なし。

## 3. 元Flet本物の全8タブ証拠とP3-384自動ゲート

既存P3-369は本物の保護された gui.py.main_window の全8実Fletタブであり、元ソース33件固定、各画面のPrintWindow8枚・実クリック・安全所有終了を証明。P3-370は管理root内生成NiceGUIの実8タブ・CDP画面8枚。双方1768×882、DPI96/DPR1。ただしFletの空FakeDB/120作品fixtureとNiceGUIの画像701/推薦201–213等は別データ、カメラもPrintWindow対CDPで異なり、視覚MAEや全機能比較は未合格。

新ツール tools/check_stage5_true_flet_parity_gate.py は保存済み両製品の8×PNG/metadata SHA、実gui.py固定SHA、起動終了、開始データ、寸法、撮影、操作/設定/状態保持/異常復帰と独立証拠を明示的に検査。r1では原gui.pyのSHA定数を誤転記したため表示0の誤診断が生じたが、r2で訂正し表示のみ8組、完全比較0組と確認。いずれも既存証拠は上書きしていない。

独立worker-1はこの新チェッカー自身のP1を発見：SHAが一致する自己発行の偽JSONと架空独立reviewでSCOPED_PASSを発行可能。新tests/test_stage5_true_flet_parity_gate.pyに、8×6件のSHA-valid自家証拠を生成する負例を追加。修正前は偽SCOPED_PASSを観測し期待どおりpytest 1FAILED/exit1。修正後はtyped actual events・source SHA・有界8MiB読取に加え、非自己発行の原Flet所有PID/終了・共通fixture bytes・独立レビュー信頼rootが未成立な間は無条件にPASS禁止とする。未達でexit0を返してしまう独立P2指摘もCLI exit1へ変更した。

後続worker-7が新P2 test gapを指摘：初回RED fixtureのevents=[null]ならtyped events検査だけで落ちるため、信頼rootのfail-closedの独立回帰にならない。そこでテスト専用のFlet既存8枚PNG/metaの**privateコピー**を用意し、ready状態を正常終了済みに揃えたSHAを全metaへ反映、NICE側のデータ等価/capture backendもtrueにして、全8タブ×6項目のJSONに非空 action/observed、64桁source before/afterを与えた。証拠は完全に合成・隔離され実原本は無変更。テストは各タブの未達理由が「外部独立信頼root未存在」**1件だけ**であることを要求するため、独立trust拒否行を削除すると架空全8 SCOPED_PASSとなって失敗する。本修正後 4 passed in 0.87s、exit0。worker-7の二度目の独立read-only再レビュー判定 **LIMITED APPROVE（このfail-closed診断器だけ）／新確定P1/P2なし**。元全機能比較の実受入を承認したものではない。

実コマンド・結果：

~~~powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_true_flet_parity_gate.py -k sha_valid_self_certified
# 修正前 1 failed / 3 deselected, exit1（期待どおりのP1 RED）
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_true_flet_parity_gate.py
# 第1修正後 4 passed in 0.74s、独立P2指摘のテスト強化後4 passed in 0.87s, exit0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_true_flet_parity_gate.py --output artifacts/p3-384-true-eight-parity-gate-r3/result.json
# UNVERIFIED / display_only_pairs=8 / complete_pairs=0 / formal_pass_added=0 / exit1
~~~

P3-384 r3 result.json SHA256=D0E334F3F9A81A9E0667A2711278E48788D32CAA1FDDF7CB645B8D2FBFE8B20C。これは**全互換成功ではない**。上記の独立P1/P2限定修正再レビューはworker-7のLIMITED APPROVEで閉じたが、独立の信用できる実機所有/操作/fixture/判定の証拠スキーマを実装し再度RED/GREENするまでfail-closed維持。

## 4. D07学習の探索率実GUI（P3-385）

元v8-D07-01の一部に対応。新tools/check_stage5_d07_preferences_browser.pyは生成onlyのNiceGUI学習タブを実クリックし、発掘設定の題材61%・絵柄23%を入力、設定保存→同client再読込→ページ再表示後の持続を実確認した。1280×720 DPR1、生成loopbackのみ。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_d07_preferences_browser.py artifacts/p3-385-d07-preferences-gui-r1
# 1 step、入力セレクタが実inputと不一致のTimeoutError、自然終了/owned0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_d07_preferences_browser.py artifacts/p3-385-d07-preferences-gui-r2
# 2 step、再読込ボタンはdata-testid無しのためTimeoutError、自然終了/owned0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_d07_preferences_browser.py artifacts/p3-385-d07-preferences-gui-r3
# 4 step、success=true、failure=null、owned_remaining=0、formal_pass_added=0、exit0
~~~

失敗r1 result SHA256=97AC9354737A8B719FE4AD6430CB5617305360FF5D4BA693F92B150A902DE6C3、r2=4B566DF6E8D43DFAD15B14D846FF1B9C7E81C87EB8D98506270964572F921965。失敗はchecker selector不一致。旧出力を残し、実製品のinput/accessible buttonラベルに合わせた。成功r3 SHA256=F9223098EC6938057F389B812B83CC84477F03304C6058B889894C049DC73037。外部通信0、JS error0、自然終了、強制停止なし、owned残存0/port解放、source/harness不変。これは**D07設定GUIの限定担当内PASS**。元Flet同条件や次回実候補割合、実Cookie/本番も含む原ID全体の正式PASSではない。

## 5. 正式152/479、Stage5と実Cookie

現行の tools/check_formal_152_coverage.py を以下で実施。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_formal_152_coverage.py --output artifacts/p3-384-true-eight-parity-gate-r2/formal-152-current.json
# success=false, total152, references479, PASS0/FAIL0/INCOMPLETE152/STALE0, candidate manifest missing, exit1
~~~

結果SHA256=289D1BA907880603527AF81BCAFF490C58D9FBC9B1D2067F8D2BB0DB8F9A3399。個別機能の152不具合ではなく、逐語expected・原開始条件・全参照・実操作と独立reviewを固定する正式候補manifestの不足。既存Stage5原正本16件の現ステータスは **4 FAIL/12 UNVERIFIED、release_ready=false**。正式0/152を増やしていない。

実Cookieの低頻度GETと限定作者差分の専用保存は既承認。ただし今回の別担当read-only preflightでは原DB/画像/ZIP/Cookie/設定全bytes保護、Win32 ACL/reparse/所有root、GET予算/429・401・403停止/POST0、ZIP→Vaultの本物の製品契約が確証不足で、新しいフル実Cookie実行を安全と判定できない。external_vault.pyは認証token暗号sidecarであって生成アーカイブ保存先Vaultと同一ではない。今回は秘密値読取り/外部GETを新規実行せず、P3-344の歴史的5GET限定成功と区別する。

## 6. D07独立P2指摘・再実GUI（P3-385 r4）

非実装者worker-6のD07 r3限定静的レビューは REQUEST_CHANGES（P2検査の取りこぼし）。第一に保存後も61/23のまま再読込しており、再読込ボタンが無動作でも通る。第二にHTTP routeだけではブラウザーWebSocket/service worker外向き試行を数えていない。第三にサーバー実実装 check_p3_residuals_browser.py と p3_residuals_fixtures.py のSHAをハーネス証拠に束ねていない。

この指摘を受け、保存後に数値欄を未保存の17/7へ意図的に変えてから、ボタン実クリックで61/23へ復帰することと「設定を読み込みました」のstatus実表示をassert。Playwright contextでservice workerを拒否、ws/wssのloopback自身以外を route_web_socket で拒否し、外向きHTTP/WS、JS・console error数を個別記録。上記2つのtransitive fixtureファイルを加えて計6本のharness before/after SHA一覧を結果に永続化した。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_d07_preferences_browser.py artifacts/p3-385-d07-preferences-gui-r4
# {"success":true,"steps":4,"failure":null,"owned_remaining":0,"formal_pass_added":0}
# D07_REVIEW_FIX_GUI_EXIT=0
~~~

新r4 result.json SHA256=F0CB3EA727B6CE21A618E654D37EECF7FCD3519E34098C77244232C66DEFF992。HTTP0、WebSocket外部0、JS0、console error0、自然exit0、forced=false、owner tree remaining0、port解放、source_changed=false、harness_changed=false、旧r1/r2/r3上書きなし。修正後の別担当worker-7 read-only独立再レビューは**LIMITED APPROVE（生成NiceGUI D07診断器の範囲に限る）／新確定P1/P2なし**。非実装者は保存済みr4結果ファイルを自身のread/globでは取得できなかったため、動的再試験を行ったと主張しない。実行/Hashの確認はprimeのCoS実コマンドに紐づく。元Flet同条件や正式D07/152全条件の承認ではない。

## 7. 実NiceGUI三画面幅96回ナビ遷移（P3-387）

新 tools/check_stage5_8tab_transition_latency.py ではL13生成fixtureサーバーのみ再利用し、720×900、1280×720、1920×1080 CSSpx、DPR1の実NiceGUI8タブを各32回、計96回クリック→aria-current確認→double requestAnimationFrameして壁時計perf_counterの往復時間を記録。full-page横はみ出しも各回検査、外部HTTP/JS error0、自然exit0、forced0、owned残存0、port解放、source/harness不変。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_8tab_transition_latency.py artifacts/p3-387-8tab-latency-r1
# success=true、samples=96、failure=null、owned_remaining=0、formal_pass_added=0、exit0
~~~

証拠 artifacts/p3-387-8tab-latency-r1/result.json SHA256=1DE416DC9B8A0630DB102C5F123B151F0076A96D98BEA23B5CA1028A8289174B。実測：

| viewport | p50 ms | p95 ms | max ms | 実操作 |
|---|---:|---:|---:|---:|
| 720×900 | 117.431 | 205.323 | 208.524 | 32 |
| 1280×720 | 113.993 | 193.412 | 196.021 | 32 |
| 1920×1080 | 116.598 | 194.483 | 215.343 | 32 |

実装者以外のworker-7読取監査はP1/P2確定指摘なし、**NiceGUI合成ナビ遷移の範囲に限り LIMITED APPROVE**。Playwright Python IPC・aria・2フレームを含む数字であり、Flet同一物理GDI paint、同一画面開始、定義済み30回のFlet性能、CPU/VRAM/私有メモリやWindows配布への直接優劣を主張できない。元Fletとの正規p50/p95比較はS5-12 UNVERIFIED継続。

## 8. 現ソース全pytestの実行環境区別

新規 artifacts/p3-388-current-full-pytest-r1 に対し標準テストvenvのPYTHONPATH=srcだけで全pytest実行すると、GPU推論・類似検索の5ファイルが環境のNumPy欠如により収集時ImportError。1SKIP/2warnings/5collection errors、exit2。XML SHA256=454234F5C7C90EDDB5CCC46A679DB27A3A62E580F9FC9CDB6DFF2B244C4828A0。この結果はコード/製品の5不具合と混同せず、全pytest合格とも扱わない。保存したresult.xmlを上書きしない。

過去P3-351の正規フルpytestは既存numpy sitecustomizeとPV_TEST_CODEC_PYTHONを併用していたことを照合。既存shim SHA256=BABCB415729575886977EB1EFB16300C672973D08E26C764F19C39C5F3E30ECB、NumPy version2.3.5を同一既設venvで正常importできることを実確認。新しいr2を以下の正規環境で全pytest実行し、結果を取得した。

~~~powershell
$env:PYTHONPATH=((Join-Path (Get-Location) 'src')+';'+(Join-Path (Get-Location) 'artifacts/p3-348-full-regression-20261030-r1'))
$env:PV_TEST_CODEC_PYTHON='C:\Users\nat\miniconda3\envs\stenv\python.exe'
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q --junitxml=artifacts/p3-388-current-full-pytest-r2/result.xml
# 4,381 passed / 6 skipped / 608 warnings / 0 FAIL・ERROR / 604.37秒 / exit0
~~~

証拠 artifacts/p3-388-current-full-pytest-r2/result.xml SHA256=0A80958AB125B97B4D804C28740E9DFDF32279E78D22E3CB1BB1EB018AE0410A、同console.txt SHA256=D7DE2821F671847C2628591676B8F81905D5C2F069A4FFF1C6723E3A4975506B。XML tests4387、failures0、errors0、skipped6、duration603.883秒。過去Stage5 full-r2の4260PASSとは異なる現ソース・追加テストの実再回帰として区別し、旧成功を上書きしていない。6 SKIPは以下のWindows symlink作成権限／OS環境で、本候補の全試験完全GREENには換算しない。

1. tests/test_diagnostic_failure_isolation.py:34
2. tests/test_egara_inference.py:132
3. tests/test_failed_record_dismissals.py:454
4. tests/test_frozen_source_pin.py:188
5. tests/test_managed_downloads.py:252
6. tests/test_novel_storage.py:121

608警告には既知のpathlib PurePath.is_reserved()廃止予定（多数）、Starlette/httpx互換警告、duplicate ZIP entry、pytest record_propertyとxunit2不適合を含む。警告0・全OS達成とは主張しない。実行中は本プロジェクトの製品srcを編集せず、終了後HEAD de3618f6・ステージ0・関連pytestプロセス0を再確認した。最終の全試験ゲートは6SKIPと原条件残存のため未合格。

## 9. 本物gui.pyの同一生成入力・初回実操作候補の具体化（実行未了）

別担当worker-2の独立READ-ONLY実コード/保存票調査で、最初に安全な条件を揃える対象は **失敗リスト** と特定した。元Fletコピー本物 gui.py のinline failedタブ（source-copy/gui.py:4078-4390,6337）とNiceGUI現 FailedRecordsView が実機能経路。共有の正本生成データは現NiceGUIの failed_records_fixtures.py:6-17 のtyped FailureRecord **17件**。元Fletのbuilder_probe.py:20-37のFakeDB.get_failed_jobs()に、元コード側の行辞書型へ明示変換した同17件を専用新root内だけで供給する。NiceGUI既定demoサービス services.py:2071-77 は同fixtureを読むが、P3-370の生成管理rootとは別物。managed利用の場合は failed_queue SQLite と各保存rootの分離（services.py:212-224、failed_records_repository.py:17-25/90-148）を満たす必要がある。

双方で「17件表示→実UI検索 failure-0001→1件/通信失敗カテゴリ/元の2行エラー表示→検索解除で17件」を実施する。元Fletは実 gui.main_window を必須としplaceholder、FakePage、純関数検索成功、画像だけの推測を代用しない。コピーした元33ソースSHAの初回/終了後照合、cookie抽出/実worker/外部API遮断、元データアクセス禁止が必要。source-copy/gui.py:4207-25,4288-4309,4311-25,4329-43 の実再試行/削除/CSV等のボタンは安全所有の保証がない限りクリック禁止。launch_true_gui.py:65-106の既存auditとFlet PID/birthの固定を使い、ネイティブ実owned HWND/GDI捕捉、DPI96、client1768×882、同一画面・時刻・開始件数・検索順で比較し、NiceGUIもheadless CDPではなく所有ネイティブGDIで撮る。

重要な新しい安全留保：import_guard.py:44-85のHERE配下許可にはsource-copy領域も含まれ得る。元コピーは事前SHA検査だけでなく書込禁止と終了後全33件SHA再照合を追加してから起動すること。Flet launch本体は既に成功しているが、この**17件同一入力・同一GDI比較を今回実施済みとは主張しない**。既存P3-369/370成功を繰り返さず、新条件の証跡のみ新rootへ出す。

## 10. 未完了工程・判断条件

元Flet実gui.py保護コピー8タブの合成FakeDBをNiceGUIの実管理fixtureとデータ型・作品数・初期状態まで一致させ、同じDPI/物理GDI撮影/操作順で実操作を測ること。画面・設定・状態保持・エラー復帰ごとの原152参照と対応する出力・SHA・独立レビューを固めてP3-384信頼rootを別途実装する。非競合GUI/異常/性能/配布は同時に進める。

未達：他担当dirtyの正規統合、U restart r2 novel error根拠、P1/P2全製品独立最終review、実Cookie安全通信と原本全bytes同一性、全GUI UI/UX、30回p95・CPU/メモリ、全pytestと6 OS privilege SKIP、CI/PR、Windows最新ソース配布、本人受入。既定U01モード・X07移動など利用者への仕様変更は影響と推奨案を出し、承認まで変更しない。**全ゲートPASS前に完成/main統合はしない**。今回の選択commit範囲は本書＋2 tools＋1 test、pushなし。
