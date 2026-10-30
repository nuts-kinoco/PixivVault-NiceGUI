# P3-370: K14 P2修復・原Flet実製品隔離境界・S07/S08未測定GUI実操作

2026-10-31 JST。対象ブランチ `phase11/p3-vault-cleanup-race-20261028`。作業開始HEAD `829636b652115476e6725a75968b00f533257e7a`、staged0、stash空。V02/P3-348および共有監査文書の既存dirtyは所有せず、編集・kill・stageしない。実Pixivへの通信・実Cookie/元DB/元画像/ZIPへの操作・追加worker/model/reviewer・main統合・pushは行っていない。旧成功K02/K04/K08/L13も再実行していない。

## 1. 先行P3-366 / K14 の2件P2故障再現と修正

レビュアーP2：ページ2/3追加後の総件数と最終IDだけでは中間の「重複57/欠落58」が通ってしまう。失敗後の `more_grid_child_after_failure=True` は実DOM確認のない固定値で、ボタンのグリッド外脱落を見逃す。

新規 `tests/test_stage5_k14_checker_guards.py`：

- 100件・120件について中間ID58を57へ置換した人工故障は、総数と末尾IDが正常のまま。旧受入条件なら通ることをassertし、新しい全ID順序・一意性・期待集合照合はAssertionErrorとする。
- `.pv-more-cell` を `.pv-grid` の外へ移す実Playwright DOM故障で、旧固定trueなら見逃す状態を再現。新しい実DOM検査は拒否する。
- ボタンがグリッド内でも末尾セルでなく先頭へ移動した場合も拒否。

修正**前**の実コマンド：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_k14_checker_guards.py
```

結果：`ImportError: cannot import name 'validate_card_ids'`、`K14_NEGATIVE_BEFORE_FIX_EXIT=2`。これは修正前の不足関数によるtest collection failureであり、製品実行のFAILではない。旧判断の盲点を人工データとDOMに再現してテスト定義。

修正：`tools/check_stage5_k14_more_browser.py` に `validate_card_ids(actual,count)` を追加し、全 `data-work-id` が期待列 `1..count` と一致し、かつ一意・期待集合一致することを page1=50/page2=100/page3=120/タブ往復120/ページ4失敗後120の**全段階**で検証する。`inspect_more_grid(ranking,expected_count=120)` は実DOMから grid1、moreセル総数1、直下子1、末尾子、card120、ボタン存在・有効・元表示文言を測り、未達ならFAIL。無条件trueを削除した。

修正後：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_k14_checker_guards.py
# 3 passed in 1.11s, exit 0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_k14_more_browser.py --output artifacts/p3-368-k14-p2-fixes-r1
# success=true, 7 steps, failure=null, owner remaining=0, K14_P2_GUI_EXIT=0
```

新専用GUI結果 `artifacts/p3-368-k14-p2-fixes-r1/result.json` SHA256 `f536d27bef3a4e93d2499c784128206968b78409426d757a07a34f7cbc189dd5`。各段階 `observed_work_ids` が50/100/120/120/120件、全段階で期待列一致。失敗後 `more_grid_layout_after_failure` の実値：

```json
{"grid_count":1,"total_more_cells":1,"direct_child_count":1,
 "last_element_is_more":true,"card_count":120,"button_present":true,
 "button_enabled":true,"button_text":"もっと見る"}
```

実出力 `normal_exit=true`、`forced_cleanup=false`、`exit_code=0`、`observed_tree.remaining_count=0`、`port_released=true`、ブラウザ外部通信0、JS例外0、`source_changed=false`、`harness_changed=false`。旧 `p3-366-k14-more-r1/result.json` SHA256 `8c549505c167e8a7b4de5be0d07aad407d4fca09a3854ee8c4a1528b676305b8` を**書換えず**、旧PASSの検証不足2件と新PASSの相違を維持する。

修正後の関連回帰コマンド：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_k14_checker_guards.py tests/test_ranking_ui.py tests/test_ranking_control_autoload.py tests/test_ranking_failed_conditions.py
# 19 passed in 4.25s, K14_RELATED_REGRESSION_EXIT=0
```

固定原K14の `v8-K14-01..05`（参照5）へ今回証拠を結合。実測：末尾セルと非自動追加、手動ページ2/3追加・正常/失敗時の状態、120件タブ往復。全toolbar操作網羅・30回以上の物理遷移p95・元Flet同条件完全比較・独立レビューは**未達**。`new_formal_pass=0`、実元製品機能全PASSとはしない。K14 P2のchecker修正・担当内新GUI受入までは完了、独立解消判定は未着手。

## 2. P3-365前操作を再実行せず回収

`artifacts/p3-365-flet-fullshape-r1/flet-close-receipt.json` SHA256 `859748bddba2eab1da5e12d7a1d288eb7be560260909d70ed90ba3103e94bc82` により、別担当所有Flet synthetic PID122800は所有Window UIA Close・**exit0**、Python残存0。 `artifacts/p3-365-matched-shell-r1/result.json` SHA256 `48e9dd4bcd85234c59cc58585e180c761943ef6d3f24f0fd46947a7a98802667` を回収。Flet合成親シェルと現NiceGUIフルシェルを生成120作品、1784×921px、ランキング→学習→ランキングの操作順で測定済み。Flet実DPI96 / NiceGUI DPR1、撮影はPrintWindow/CDPの異なる経路、平均画素差44.243は診断値のみ。`full_original_product_GUI_parity_pass=false`。

元Fletの**実製品コード全タブ**を安全に直接起動するには、現在の隔離ハーネスでは不足。元 `S:/Projects/PixivVault/gui.py` SHA256 `731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61cd6acbecd84e939` の静的読取りで：

- `gui.py:1–44` は元 `discovery_tab.py`、`library_tab.py`、`recommend_tab.py`、`unified_download_tab.py`、dedup等を直接import。固定 `legacy_baseline.py` の7-file isolationはそれらの依存・実所有を隔離していない。
- `gui.py:462–470` ではdb未注入時 `Database()` を開く。`:6160–6167` は統合タブ・重複・履歴の実DB状態を自動読取り。`:6335–6347` は `_wake_embed_worker_bg()` とCookieを確認する `threading.Thread` を起動する。Cookie処理（`:6216–6237`）には自動抽出・更新経路も含む。
- 現P3-365の他7タブはプレースホルダー。全製品本体 `gui.py` の実import/実行ではない。旧Flet原本・Cookie・元DB・推論workerへのアクセスを遮断する**全依存への実効的フェンス**がないまま起動してはならない。元コードの読取/AST・既存レシート回収は実施したが、実Flet全製品GUIの安全起動・撮影・全タブ同条件PASSは**未実行**。上記依存に限って保留。

同条件比較の残要件：全9元タブと実動作を元Fletコードから隔離、全元DB/Cookie/ZIP/画像と実worker所有の安全ゲート、同じ生成データ/初期タブ/ウィンドウ外形・クライアント/DPI/撮影backend、実クリック・ESC・focus・Viewer・保存、パフォーマンスp95。元製品の初期タブは統合（`TAB_INITIAL=TAB_UNIFIED`）、P3-365の合成枠はランキング初期表示なので開始状態も一致しない。新しい模造UIや純関数一致をPASS代用としない。比較に必要な独立ゲートは未達のまま。

## 3. 非依存原S07 / S08 設定GUIを新規実測

既存 `artifacts/p3-363-formal152-gap-index-r1/all-152-original-id-gaps.json` の未測定からS07/08を選定（索引再生成なし）。`tools/check_stage5_s07_s08_prefs_browser.py` を作成し、専用生成root `artifacts/p3-369-s07-s08-prefs-r1` のみでPlaywright実操作：

1. 初期小説 `epub` / うごイラ `gif` を私有DBから確認。
2. 設定ダイアログで小説 `TXTのみ` (`txt`)・うごイラ `MP4動画` (`mp4`) を実セレクト。
3. 保存ボタン1回で両設定を1 CAS revisionで専用DBへ永続化。値 `txt/mp4`、revision `0→1`。
4. ダイアログ内の「再読込」で値保持、ブラウザreload→設定を再開して値保持。
5. 別NiceGUI clientから両設定が保持されていることを確認。

実行：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_s08_prefs_browser.py --output artifacts/p3-369-s07-s08-prefs-r1
# success=true, 6 steps, failure=null, owner_remaining=0, S07_S08_R1_EXIT=0
```

結果 `artifacts/p3-369-s07-s08-prefs-r1/result.json` SHA256 `2784d228acdb2e087dc6dc5914ca7d87014420e04d1f94a41788643862b58b5a`。実Pixiv通信0/JSエラー0/実保存receipt0、自然終了exit0、所有子孫0、port解放、source/harness SHA不変。原 `v8-S07-01`（UG:105 小説EPUB/TXT/BOTH）、`v8-S08-01`（UG:106 うごイラGIF/MP4/frames）について**UIから設定選択・保存・復元の限定条件のみ担当内合成PASS**。ほかの選択肢切替の実操作・次回の実保存に適用される最終ファイル形式・元Flet実GUIの同条件対照は未検証。正式2原操作PASSではない。

## 4. 保全とゲート

- P3-368 / P3-369とも実Cookie・旧DB・元画像/ZIP・元設定は参照/変更していない。保管先は新規生成データrootだけ、強制終了なし、外部通信0。
- K02/K04/K08/L13の既成功GUI試験再実行なし。P3-365別担当の所有PID/ファイルに介入なし。
- 実Cookie依存の操作は製品全体の必須残件として保持。全bytes原データ保全・保存ownershipと本人の通信境界が確認できるまで未実行。
- 正式152操作・479参照、全PASS **0/152**、Stage5 **4 FAIL / 12 UNVERIFIED**、release-ready=false。担当内の限定合成GUI成功と独立P1/P2解消確認・本人受入・CI・リリース・main統合は別の未完工程として保持。
- `git diff --check` 全体では既存の他担当 `docs/PROGRESS.md` の空行警告があるため、そのファイルは変更しない。今回の選択差分だけをstage/commitし、他担当dirtyを巻き込まない。 `artifacts/` は既存gitignore対象なので、実行時証拠はSHA付きローカル保存、コードとこの検証文書のみを版管理する。
