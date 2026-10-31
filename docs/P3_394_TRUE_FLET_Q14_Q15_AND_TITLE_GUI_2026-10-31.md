# P3-394 — 実Fletの失敗理由カテゴリとタイトル検索ANDをNiceGUIと同一生成条件で受入

2026-10-31 JST。開始HEAD bf78e2722cb64f14dbd5dd916ce0d53f804f14d8、branch phase11/p3-vault-cleanup-race-20261028。今回の追加は新規 tools/check_stage5_failed17_filter_and_search_gui.py、tests/test_stage5_failed17_filter_and_search_gui.py、本書に限定。元Flet原本、DB、画像、ZIP、Cookie、元設定、実アカウントは変更しない。実Pixiv GET/POST 0。元ソースの保護コピー33件は起動から閉じるまでWin32 FILE_SHARE_READのみのハンドルで固定し、終了後のSHA不変を確認。他担当dirtyを編集・stageしない。

## 元Flet本物gui.pyでの実クリック（P3-392）

旧P3-389をただ再実行したものではない。これまで元Fletから未測定だった「失敗理由カテゴリと日本語タイトル検索のAND、カテゴリ解除」を新条件として、保存済み保護コピーの実 gui.main_window のinline失敗リストと同じgenerated17 fixtureを使って測定した。元Flet本番データ・Cookie・実ワーカー・外部APIは使わない。

実行：既設Flet用stenv-flet1のpython -X utf8 -B tools/check_stage5_original_failed17_native.py artifacts/p3-392-real-flet-failed17-filters-r1。実本体ready PID122740、full8タブ構築、source_workers_started=0。正規CoS Desktopで本人所有PixivVault実FletウィンドウHWND9308166を操作。実操作およびアクセシビリティ観測は以下の順序。

1. 生成17件の失敗リスト→「通信」チップを実クリック：対象3件（failure-0001/0007/0013）に減少。
2. そのチップを選択したまま、タイトル「架空の失敗作品 007」を実入力：1件（failure-0007）。
3. タイトルを「架空の失敗作品 002」へ変える：該当作品は生成fixtureに存在するが通信カテゴリの外なので「一致する失敗はありません」。
4. 実「検索を消す」をクリック：通信カテゴリを保持し3件へ復帰。
5. 実「すべて」チップをクリック：全17件へ復帰。

上記5ステップは各回の get_window_state の原生アクセシビリティツリー（source=Chat_On_Steroids_Desktop.get_window_state）を専用rootに別JSONとして保全。順序frame-41/43/44/46/47、実ウィンドウHWND9308166同一。「再試行」「削除」「CSV」「全件クリア」はクリックしていない。タイトルバー実クローズで実行exit0、元ソース33 SHA不変、関連PID0。

保存済み相対pathとSHA256：

| 証拠 | SHA256 |
|---|---|
| P3-392/actual-original-full-gui-ready.json | F3B5393900F65586229763D809AB09D8E19B2781628EDC5FEF634F96033374A2 |
| P3-392/generated-failed-17-source-guard.json | A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6 |
| P3-392/uia-communication3.json | 1D5238643FA9DB4371336F21DE2CA53AFD94A945CA99DA873428B7147F79BDE5 |
| P3-392/uia-communication-title007.json | 3BB40B98B5F55489AD7BA6C1077819DF1559E8D9586858C87843C18A0C96F202 |
| P3-392/uia-communication-title002-nomatch.json | F6F14553EE18A6464A7A5EABA1A616CE2AA66D1CCDE3D69153FF10F9C8836178 |
| P3-392/uia-communication-restored3.json | F7A6B6B8F48041D55C8923E6EE59C3899C99441F976CEA2B0C5F69922B1A846A |
| P3-392/uia-all-restored17.json | A4400E141A64773F823C458A54E84A8592761C23318F6CCAD4435CB06145EF02 |

## NiceGUIで同じ生成入力・操作結果（P3-393）

追加ツール tools/check_stage5_failed17_filter_and_search_gui.py は、既存の正規P3-390実Flet同条件checkerを土台とし、**新しいFlet P3-392の5個の実UIA状態**が存在して同一ウィンドウ・frame単調順・各段階の期待結果を保持することと、元33ソース/実gui.py固定SHA・元生成17件fixture fingerprint一致を起動前に確認する。初回P3-389の元GDI二画面にも結合し、別名placeholder等で合格させない。独立第三者署名の完全な証拠trust rootは依然として別ゲート。

実行：S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 -B tools/check_stage5_failed17_filter_and_search_gui.py artifacts/p3-393-nicegui-failed17-filters-r1。現NiceGUI実製品の失敗一覧へ同じfixture17件を読み込ませ、実Playwrightで通信3→タイトル007で1→タイトル002で0→検索解除で3→全カテゴリ解除で17、計6段階（開始17を含む）を実操作。CSS 1770×884、DPR1.75、画像3098×1547、画面横はみ出しなし。ただしキャプチャはCDPで、元FletのPrintWindowと**撮影方式が違う**。

- result.json：artifacts/p3-393-nicegui-failed17-filters-r1/result.json SHA256 **79AF744733A26C90551393DEA0BA93C8947A1DF536376FB6DCECB512FC56511D**
- success=true、6steps、failure=null、formal_pass_added=0、元Flet生成fixtureと現在NiceGUI生成fixture SHA同一、外部HTTP=0、外部WS=0、JS=0、console errors=0、元fixture DB不変・source_changed=false・harness_changed=false。
- app normal_exit=true、forced_cleanup=false、owned子孫0、port released=true、checker exit0。全製品本番起動ではなくloopback専用合成製品の実ブラウザ受入。
- 生成CDP画像（検索1件）SHA256=4CAC70350E62A502A9F3DA9CC7E8F06D1D223CD361F26A05500EEAA3389BF109、解除後17件=5BC4DAAB96675BD182BB12F5CA145B901F133C22F17ED1B8B3323BB2BF09DC7F。

試験コマンド：PYTHONPATH=src、python -X utf8 -m pytest -q tests/test_stage5_failed17_filter_and_search_gui.py。初回の実結果は **7 passed in 0.68s / exit0** だったが、後述のP2を発見したため、この時点の検査器は解消済みと扱わない。検査器はFlet観測に欠落・位相違い・操作結果不足・frame逆転等があるとNiceGUI起動前に拒否する。旧P3-390成功GUIを重複起動していない。

## 固定元操作・独立レビュー・残るゲート

- **Q14**：固定v8参照 v8-Q14-01/02/03、通信カテゴリの3件絞込のみ両実GUIで限定確認。残りの分類全ての実クリック・AND全種類、画面幅別/再起動/異常復帰、原逐語期待と独立scope-full判定は未完。
- **Q15**：固定v8参照 v8-Q15-01/02/03/04、同一17件について日本語タイトル007/002、カテゴリとのAND、no-match、二段解除を実GUI確認。ID検索は別のP3-389/390、頁またぎ・入力競合・元全条件は未完。
- 正式152件・479原参照の分母・証拠不足索引は改変していない。今も正式0PASS/152 INCOMPLETE、Stage5 4 FAIL/12 UNVERIFIED、release_ready=false。Flet/NiceGUI両方の同一native GDI、全8タブ同一実製品入力、保存形式、実Cookie、429・認証エラー、全CI/Windows配布/独立最終レビュー/本人受入が未達。
- 今回の個別検証に対する非実装者の限定read-onlyレビューは別担当worker-7/worker-2へ依頼済み。最新修正後の独立解消判定は未回収。新P1/P2が指摘されたらまずREDテスト→最小修正→再受入、レビュー前に今回の全体正式PASSを計上しない。元データ/秘密値/アカウントへ追加アクセスせず、push・main統合を行わない。

## P3-394後続：UIA自己整合偽装のfalse-GREEN P2（修正済み・独立判定待ち）

P3-393 r1後、`verify_flet_filter_actions()` がP3-392の5個のUIA JSONについて単に同じwindow ID・位相・表示文言が揃うことだけで通しており、**すべてを一貫して改ざんした偽の原Flet証拠を受理する**P2を発見。これは製品GUI本体の不具合ではなく、比較検査器の信頼境界の不備である。

- RED：`tests/test_stage5_failed17_filter_and_search_gui.py::test_self_consistent_forged_owner_and_uia_text_must_not_pass`。生成専用tmpへ原票を複製し、UIAのwindow IDを偽IDに統一して本文にも偽表示を追加する。旧checkerでは `Failed: DID NOT RAISE RuntimeError`、**1 failed, 7 deselected / exit1**。これは旧コードが不正証拠を通したことを示す期待REDであり、製品回帰FAILを隠していない。
- 最小修正：`tools/check_stage5_failed17_filter_and_search_gui.py` に `P392_FIXED_EVIDENCE_SHA256` と、正規Desktop実観測HWND `9308166` を固定。元Fletのready/guard/5 UIAの合計7原票について、実SHA256・通常ファイル・symlink拒否・サイズ上限を先に検査し、各UIAが当該固定HWNDであること、5位相の期待/欠落文言とframe順序、元ソース33件不変、同一fixtureを要求する。**第三者署名の完全なtrusted rootではなく、P3-392実観測原票に限定した診断用binding**。
- 局所GREEN：同ファイルを指定したpytestで **8 passed in 0.69s / exit0**、識別出力 `P394_UIA_HASH_PIN_GREEN_EXIT=0`。追加負例もGREENで拒否された。
- 修正後の新しいNiceGUI実GUI確認：`artifacts/p3-393-nicegui-failed17-filters-r2/result.json`、SHA256 **5E1DED2B1F1A46316B2535F022D835E2C57B3CE04F0296F74D1ABFE101AB3785**。`success=true`、6 steps、failure=null、checker exit0、同一生成fixture、外部HTTP/WS 0、JS/console error 0、正常終了exit0、forced=false、owned remaining 0、port解放、元fixture DB/source/harness不変、`formal_pass_added=0`。CSS1770×884/DPR1.75/物理3098×1547、scrollWidth1770。修正後checkerのSHA256は **581DFB8E9C8A6CA084831191CCE634628D97C373EA9D7D77632FEFB568A93934**（今回の追加変更前後の現物確認時）。
- **r1とr2は別root・別時点の証拠。r2の成功レシートとSHAは2026-10-31の引継ぎ後に読取再照合済み。成功済みr2 GUIは再実行していない。** P3-392固定7原票の実SHAと元Flet本番`gui.py`のSHA `731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939` も引継ぎ後に一致確認。

独立workerの修正後レビューが完了するまでは本P2を正式な独立解消済みと主張しない。元FletのPrintWindow/native GDI対NiceGUIのCDP撮影方式差、原152/479期待の全条件、同一native方式の全8タブ・原実処理・実Cookie/ZIP/Vault・障害復帰・配布・CI・本人受入は未達のまま。正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false` を維持する。

## 追加独立レビューworker-2による2件のchecker P2と最小修正

worker-2の実装者外READ-ONLY独立監査は、前節の固定P392 SHA/HWNDを妥当な限定補強とした一方、後続P393のcheckerに**別のfalse-GREEN 2件**を指摘してREQUEST_CHANGESとした（製品本体の新P1確認なし）。修正前のP393 r2成功を改ざんせず歴史的な限定証拠として保持し、最新修正を反映した新rootで再受入した。

1. **P2：検索0件でも隠れた空表示文言だけで誤合格**。旧checkerはtitle002入力後に `expect(failed-empty).to_have_text('一致する失敗はありません')` のみ。製品 `failed_records_view.py:237-246` では、1件等が残っても非表示のempty要素にその文字列を設定するため、検索更新が失敗した場合にも後続の検索解除とカテゴリ解除までfalse-GREENし得た。修正前の故障DOMで実Playwrightテスト `test_hidden_nomatch_label_with_live_record_must_not_pass` を実行し、**1 failed, 8 deselected / `Failed: DID NOT RAISE <class 'AssertionError'>` / RED exit1** を確認。修正後は検索値title002、通信カテゴリ `aria-pressed=true`、件数 `0 / 17 件`、表示レコード0、empty可視、文言一致を同時に要求。正しい空状態がPASSする正例も追加した。固定Flet P392のno-match実UIAには「再試行できるものがありません」があり、`failure-0007` は無いことを原票で確認し、その必要・排除文言も検査に追加した。
2. **P2：正常終了のserver-lifecycle原票未照合**。以前は親プロセスexit0・port解放・server子孫0だけをsuccess条件とし、実serverが保存する `lifecycle/server-lifecycle.json` を判定しなかった。修正後の `verify_failure_server_lifecycle()` は固定listener PID、`services_closed=true`、`shutdown_requested=true`、`external_denied=0`、生成DB SHA一致、通常ファイル・サイズを必須確認し、success条件に加えた。保存済P393 r2実票の正常値を確認。PID偽装、未close、shutdown未要求、外向き拒否発生、DB SHA不一致、票欠落の**6負例**と正常票正例を追加した。このP2は独立静的証拠で指摘されたもので、修正前の全GUI故障再実行はしていない。

局所再試験：`PYTHONPATH=src; python -X utf8 -m pytest -q tests/test_stage5_failed17_filter_and_search_gui.py tests/test_stage5_failed17_other_categories_gui.py`。最新**24 passed in 7.15s / exit0**。これはP394・P396に属する24局所試験の合計であり、元152操作のPASS数ではない。

P393 **r3（最新checkerでの新GUI実操作）**：`artifacts/p3-393-nicegui-failed17-filters-r3/result.json` SHA256 **C7B76D0E6A7F45892839DEEF7BA2F5AFC8465A3D80AA54866B5E6E8921F67984**、checker **exit0**、success=true、6実操作、failure=null、formal_pass_added=0、正常終了exit0、強制終了なし、server-owned子孫0、port解放、`lifecycle_verified=true`、外部HTTP/WS 0・JS/console error0、生成DBと製品ソース/ハーネス不変。最新checker SHA256 **24270771A07DD86506511E8155CF06B60EC574E959AC1F0CBA756CCBB78785B2**。NiceGUIはCDPでありnative GDI撮影同等とは言わない。worker-7の修正後最終独立判定は引き続き未回収。**release_ready=false、正式0/152、479原参照、Stage5 4 FAIL/12 UNVERIFIED**を維持。
