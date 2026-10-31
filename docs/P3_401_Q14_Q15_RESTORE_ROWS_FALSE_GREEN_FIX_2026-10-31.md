# P3-401 — P394 Q14/Q15検索解除後の表示行false-GREEN P2を診断器で塞ぐ

2026-10-31 JST、P394既存commit `2e497d0abf706a7e12177a0bd385c0c1cd3fead0` に対する**後続の限定checker修正**。独立worker-8のREAD-ONLY中間監査が、現 `tools/check_stage5_failed17_filter_and_search_gui.py` の検索解除後「3 / 17 件」およびカテゴリ解除後「17 / 17 件」のラベル文字列だけで成功とする点を指摘。表示行が消えたり古いIDを残した状態で件数だけ更新されても、旧checkerは6/6 steps success=trueにし得る。この診断器P2は、製品本体が実際に失敗したという意味ではなく、以前のP393 r3成功実測を否定するものでもない。

## RED（製品原本を使わない故障DOM）

新しい `tests/test_stage5_failed17_filter_and_search_gui.py::test_correct_counter_but_missing_visible_rows_must_fail` は、Playwrightの生成HTMLに正しい件数カウンタ・aria category・頁表示を置きながら、`failed-rows` を空のままにして `assert_restored_rows()` へ渡す。2ケース（通信3件へ復帰、全17件へ復帰）とも、修正前の関数は件数ラベルしか検証せず意図的な失敗を拒否できなかった。

```text
2 failed, 17 deselected
Failed: DID NOT RAISE <class 'AssertionError'>
P401_RESTORE_ROWS_RED_EXIT=1
```

REDは新しいP2を具体的に再現するための失敗であり、既存製品GUIを失敗させたわけではない。実Flet/P393 r3 GUIは再起動していない。

## 修正と関連GREEN

`assert_restored_rows(root, all_categories)` を、件数カウンタだけでなくカテゴリ二つの`aria-pressed`状態、頁`1 / 1`または`1 / 3`、DOMの`failed-record`件数、行の`data-work-id`セット（通信3は0001/0007/0013、全17はページ先頭0001～0008）、通信3状態で非対象ID不存在、空表示非可視まで同時検査するよう補強。正常な生成DOMの正例2件も追加した。元Flet本物の固定UIA証拠と既存同一生成fixture/所有/終了の全境界は弱めていない。外部通信・実Cookie・元DB・製品ソースの変更なし。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_failed17_filter_and_search_gui.py tests/test_stage5_failed17_other_categories_gui.py tests/test_stage5_failed17_id_category_tab_gui.py
# 36 passed in 20.53s / P401_RESTORE_ROWS_GREEN_EXIT=0
```

修正後の新GUI実行（旧r3を上書き・再送せず別root）：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_filter_and_search_gui.py artifacts/p3-393-nicegui-failed17-filters-r4
# P393_R4_CHECKER_EXIT=0
```

新 `artifacts/p3-393-nicegui-failed17-filters-r4/result.json` SHA256 **`8ADD3FB5573E834EAD21CF7CA898CACF11E186D5E70329CE9200B965A681B789`**、success=true/6 steps/failure=null、formal_pass_added=0。正常終了exit0、forced_cleanup=false、server子孫remaining0、port解放、`lifecycle_verified=true`、外向きHTTP/WS 0、JS/console errors0、生成DB不変・source/harness_changed=false。新checker SHA256 **`722E51E30AE71DF45548504DEE1F7C8A40B1E611E54B712E02087D9B6189AE49`**。旧r3結果やP392実Flet UIAは保存済み正本として維持。関連PIDの最終再列挙で該当プロセス0。

## 受入範囲

worker-8の最終判定と追加指摘は別に回収する。修正後の独立解消が確認されるまでは **P2独立CLOSEDを宣言しない**。P399のID×カテゴリ/タブ往復は別の新checker・別証拠であり、このP2への修正をQ15全体の形式的完了に読み替えない。本物FletのPrintWindow/native UIAとNiceGUI CDPの撮影方式差、元152件逐語expected・479参照、実Cookie/ZIP/Vault、安全ゲート、Win配布/CI/最終本人受入は未達。正式 `0 PASS / 152 INCOMPLETE`、Stage5 `4 FAIL / 12 UNVERIFIED`、`release_ready=false`。push・main統合なし。他担当dirtyは一切編集・stageしない。

## worker-8最終中間からの追加指摘：初期3件/title007の過剰表示もfail-closed

worker-8が元P394をREAD-ONLYで最終レビューした際、上記の検索解除後の2段階に加えて、**初期通信3件とtitle007で1件の段階も、件数と一部文字列だけでは余分な行を許す**ことを指摘した。最終結論は当該P2について `REQUEST_CHANGES`、ただし既存P393 r3で実観測された製品障害・秘密漏えい・所有逸脱を示す証拠ではない。

- 解除後2状態の故障DOMは前節のREDを確認。初期17と通信3の受入に同じ `assert_restored_rows()` を使い、件数/aria/page/行数と対象ID集合/空表示非可視を検査するよう統合。
- title007の1件表示については、`failed-count='1 / 17 件'` と `failure-0007` の文字列を正しく置きつつ**余分な `failure-0013` 行も表示する**故障HTMLを生成。旧 `assert_title_007_filtered_ui()` は拒否しないので、**`1 failed, 21 deselected`、`DID NOT RAISE <class 'AssertionError'>`、RED exit1** を実確認。
- 修正後title007は入力値、通信カテゴリの選択状態、件数1/17、頁1/1、表示`failed-record`正確に1行、行ID`failure-0007`、空表示非可視まで要求。正常な1行DOMの正例も追加。
- 関連全局所の最新結果は **38 passed in 26.63s / exit0**（P394/P396/P399関連3pytestファイル）。以前の36PASSやr4はその時点の履歴として保持。

新しいP393 r5 NiceGUI GUIを、再利用しない専用rootで同一17件・6操作実行：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_filter_and_search_gui.py artifacts/p3-393-nicegui-failed17-filters-r5
# P393_R5_CHECKER_EXIT=0
```

`artifacts/p3-393-nicegui-failed17-filters-r5/result.json` SHA256 **`846EB2129F9B292E261D914B5478ACF5E41D4C1AFCD69192BFC4F3203D638297`**、success=true、6 steps、formal_pass_added=0、正常exit0、強制終了なし、server-owned残存0、port解放、`lifecycle_verified=true`、外部HTTP/WS/JS/console0、元生成DB・製品ソース・ハーネス不変。checker SHA256 **`FF331730A7C46A0F64998D292CAC35E8881D249D57122B224B67C8F5B8321629`**。独立修正後レビューで別途確認するまでは承認済み扱いにしない。

## worker-8の追加P2：正しいID/行数の非表示DOMが通る問題

worker-8によるr5の独立再監査は前述の行数・ID・頁に関する故障は解消と評価したが、`to_have_count` が隠れた行も数えるため、`failed-rows` が `hidden` のままで表示が空のケースに対して **REQUEST_CHANGES**。したがってr5時点の完全な独立CLOSEDは付与しない。

- 新負例：元の正常な対象行を全件残し、**親 `failed-rows hidden` だけ付与**する3つの生成HTML（通信3、全17の先頭8、title007の1）。旧checkerの各ヘルパーは拒否しなかった。**3 failed, 23 deselected、`Failed: DID NOT RAISE <class 'AssertionError'>`、`P401_ROW_VISIBILITY_RED_EXIT=1`**。
- 最小修正：各 `data-work-id` 行の `expect(row).to_have_count(1)` に加え `expect(row).to_be_visible()` を必須に変更。親あるいは行そのものがhidden/display:noneのままなら失敗する。元Flet全8本体や製品ソースは変更しない。
- 最新関連ローカル：**41 passed in 43.14s / exit0**、対象3pytest（P394/P396/P399）。

この修正を反映したNiceGUI実GUIは新root `artifacts/p3-393-nicegui-failed17-filters-r6` で一回実行し、checker exit0、success=true、6steps、formal_added0、通常終了exit0、強制終了なし、server子孫0、port解放、`lifecycle_verified=true`、外部HTTP/WS、JS/console0、生成DB/source/harness不変。

- r6 result SHA256：**`75AED8DDC64E6584A6C29578BBCED8189949944DB5675A71EDC1033AABCAD70C`**。
- r6 checker SHA256：**`1CBD948BD527F684365FD4CA3A85A16E86EFB70FB6693CD2D7C016A654283C72`**。

旧r1～r5は別ハーネス時点の履歴証拠として残し、上書きしていない。worker-8のr6補正後の独立結論は別に受領するまでは推定しない。元正式0/152、479参照、Stage5 4FAIL/12UNVERIFIED、`release_ready=false`は変更しない。

## 独立最終判定（worker-8、最新r6）

worker-8が実装変更せず最新checker（554行）、故障DOM負例、正常正例、P401記録、P393 r6のresult/lifecycle/2画面をREAD-ONLY再監査し、上記**同一生成17件の当該P2を scoped CLOSED / LIMITED APPROVE** と判定。新規P1/P2なし。具体的には全ての非ゼロステップに `data-work-id` ごとの `to_have_count(1)` と `to_be_visible()` を要求しており、初期17/通信3/title007/検索解除3/全17に共通適用されることを確認した。Flet元Q14/Q15の他の全期待、全頁、全実API、全8タブ、同一native撮影、リリース全体の独立受入は**対象外**。同workerは保存済み試験値/PNGを読取照合しただけで試験・GUIを再実行していない。旧REQUEST_CHANGESはこの最新限定修正に限り解消。正式0/152とrelease falseを維持。
