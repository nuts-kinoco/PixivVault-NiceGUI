# P3-400 — Q15のID検索・カテゴリAND・タブ往復を本物FletとNiceGUIで限定比較

2026-10-31 JST。作業開始HEAD `0e926a8ce75e5ac3a1aa9bac6ae28fa26d57bdb3`、ブランチ `phase11/p3-vault-cleanup-race-20261028`。目的は元Flet実 `gui.py.main_window` と現在NiceGUI実製品を、同一の**架空17失敗レコード**で比較すること。P3-392/393の通信カテゴリ・タイトル検索、P3-395/396の残り5分類は繰り返さず、**権限カテゴリ×失敗ID検索・不一致・検索解除・別タブ往復**を新しいGUI実操作として追加した。元Flet本番原本・元DB/WAL/SHM/画像/ZIP/Cookie/元設定、実アカウント、実Pixiv APIは操作しない。元Flet実再試行/削除/CSV/全件クリアもクリックしない。

## P3-398：本物Flet gui.py / 生成17件 / Desktop実操作

実行：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'C:\Users\nat\miniconda3\envs\stenv-flet1\python.exe' -X utf8 -B tools/check_stage5_original_failed17_native.py artifacts/p3-398-real-flet-failed17-id-tab-r1
# P398_FLET_EXIT=0
```

元保護コピー33個をWin32 read-sharingハンドルで固定し、本物の `gui.py.main_window` で全8タブ生成、元ワーカー0。所有Flet起動PID **122960**、Desktop PixivVault window HWND **56952522**。各操作の実 `Chat_On_Steroids_Desktop.get_window_state` アクセシビリティ状態を生成rootに別JSONとして保存。各段階の必要件数と対象失敗ID、非対象ID不在を生UIAで検査した。

| 操作/生UIA phase | 実観測 | frame | SHA256 |
|---|---|---|---|
| 初期全件 `initial17` | 17件 | 58 | `8742B079E270F7D1E4FF836D6627B83CDC697DFEC097565429AB0B5E384E26BA` |
| 権限・年齢制限 `permission3` | 0003/0009/0015、3件 | 59 | `ED0126509F9F50627A36A97435DAE506CCF4DD997E30134A357F3386EAE9581E` |
| ID `failure-0009` `permission-id0009` | 0009、1件 | 61 | `E47AF034D37A8115C389DF2350342860B6864BBD896DEF6B0E79F217FC4BC054` |
| ID `failure-0010` `permission-id0010-no-match` | 対象外で一致なし・解除ボタン | 63 | `7779D18C4EC7486851B32855F8486C2D9D3F80F9212A8A6B482B2E03972347BB` |
| 検索を消す `permission3-cleared` | 権限カテゴリ3件を保持 | 64 | `7F8180E1BCF506DBE865396065E1C3C11BD69F9C89C765E395C9023EC30E49BF` |
| キュータブから戻る `permission3-tab-return` | 同じ3件を保持 | 67 | `E10F65C701428FACE530BBB99CF927DD8A82958B1EA7503EA885EC13EA4B5B4F` |
| すべて `all17-final` | 17件へ復帰 | 68 | `B4A8C0198029EFB96D1181FF6175B0ECDC9E48BE8970323DAAB90AB1C9500A5B` |

**証拠上の限界：** キュータブへ実クリックし、途中の `ダウンロードキューログ` をDesktopでframe-65に観測してから失敗タブへ戻ったが、**その中間queueフレームの原票は個別ファイルとして保存していない**。したがって固定UIA7原票だけで全タブ操作列の独立完全証明ができたとは言わない。実操作時の観測と、後段の状態保持原票を分ける。IDの全文 `failure-0009` を検索しており、短縮部分文字列のみの実GUI条件やFletの複数ページの実クリックは今回未測定。

P398 ready `actual-original-full-gui-ready.json` SHA **`8531C20E4E425C65835D8AB574FE6051B3C77AA094DCA6584C5AC243F66DD5E0`**、guard `generated-failed-17-source-guard.json` SHA **`A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6`**。実ウィンドウのタイトルバーから通常終了、`P398_FLET_EXIT=0`、後続PID0、元33ソース不変、元本番 `gui.py` SHA **`731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939`** 不変。

## P3-399：新NiceGUI実ブラウザ受入

変更：

- 新 `tools/check_stage5_failed17_id_category_tab_gui.py`：P398 9原票を固定SHA/HWND/PID・frame単調順・必要/排除文字列・元保護コピー33SHA/同一fixtureに結合し、現実製品の失敗一覧をPlaywrightでクリック。検索値、カテゴリ aria-pressed、0件の可視化・レコード0、別タブ復帰、先頭頁と8件のDOM状態を検査。
- 既存担当 `tools/check_stage5_failed17_other_categories_gui.py`：P3-396成功済みデフォルト操作は維持したまま、オプショナル `ui_steps` / `original_verifier` で新シナリオを共有できる構造に限定変更。nonce、外向きHTTP/WS遮断、ブラウザJS/console、プロセス所有と通常shutdown、server-lifecycle、生成DB/source/harness SHAの検査は共通で再利用。
- 新 `tests/test_stage5_failed17_id_category_tab_gui.py`：元P398生UIAの固定正例、位相・原owner・表示文言・UIA一括HWND偽装・欠落の拒否。既存P394/P396の関連テストとも合わせて実行。

試験：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_failed17_id_category_tab_gui.py tests/test_stage5_failed17_other_categories_gui.py tests/test_stage5_failed17_filter_and_search_gui.py
# 32 passed in 8.60s / P399_LOCAL_EXIT=0
```

実GUI：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_id_category_tab_gui.py artifacts/p3-399-nicegui-failed17-id-tab-r1
# P399_R1_CHECKER_EXIT=0
```

P399 `result.json` SHA256 **`2BF9A3E88BA1131856E0CD728F7F2931B66ED8739B9CB1E0FADE06403AC26EA3`**、success=true、7 steps：初期17→権限3→ID0009で1→ID0010で0→検索消去で3→キューtab往復で3→全17（NiceGUIはpage1/3、表示8行）。`formal_pass_added=0`。同一生成fixture fingerprintは双方 `711c0bd7f743688ab34f3dbb83f2b4e0cb3319c4266a29f9f1419a7b6ad41ae0`。CSS1770×884、DPR1.75、物理3098×1547、scrollWidth1770。外部HTTP/WS 0、JS/console errors0、正常終了exit0、forced=false、owned server children0、port released、server-lifecycle validated、生成DB/source/harness不変。最新checker SHA `2043054959FE1005A5CC428029360EB77F3BA11D73EA89B1FB30460FB1EA20E1`、共有harness tool SHA `8B77C284BC2EA7677D1ACF73A9683FDA20672BCB68BBAB2A9A5D8F79975AA106`（両者は今回実GUI後にSHA再照合）。

**限定評価：** Fletは本物の原コード、NiceGUIは現製品で同一生成17件・同じ画面物理サイズだが、Flet側はnative Flet Desktop UIA、NiceGUI側画像はPlaywright CDPで、同一native GDI撮影ではない。**元Flet原コード `source-copy/gui.py:4090,4356-4364` は1頁8件・17件なら3頁**であり、NiceGUI側の1/3頁と同じページ数契約。P398全件原UIAは表示先頭8行を観測したが、全17件の頁ラベルの生原票は保存していないため、**同一頁表示の実測正式PASSではない**。操作結果と完全UI/UX互換は別である。ID短縮部分一致や頁をまたぐ操作、同一native視覚、再起動後状態、異常復帰、原逐語expectedと全8タブ同条件検査は継続未達。

今回をQ15原参照 `Q15-original-01/02/03/04` とQ14のANDに対応する**限定実GUI追加証拠**としてのみ記録。正式152の分母・479原参照と逐語expectedを改変しない。正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`。実Cookie/ZIP/Vault・配布・CI/独立最終レビュー・本人受入は未完。push・main統合禁止。他担当dirtyは維持。独立review結果は別に回収し、残P1/P2があれば再現REDから先に修正する。

## P3-396共通harnessリファクタ後の既存分岐の関連回帰

新P399を既存P396の `run(..., ui_steps=...)` に収めたため、今回所有の `tools/check_stage5_failed17_other_categories_gui.py` には既存デフォルト操作分岐も変更履歴が生じる。古いP396 r2成功を新ソース成功とすり替えず、**コード変更に対応する関連GUI回帰**を専用新rootで1回実施：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_other_categories_gui.py artifacts/p3-396-nicegui-failed17-other-categories-r3
# P396_R3_CHECKER_EXIT=0
```

新 `result.json` SHA256 **`C9ACC64010D769EC3B7336395D084DE27CAFCF960AD72C6A12A2E32175F9F4AF`**。既存5カテゴリ＋全17への復帰、success=true/7steps、正常終了exit0、強制cleanupなし、owned server-tree0、port解放、`lifecycle_verified=true`、外向きHTTP/WS 0、JS/console0、生成DB/source/harness不変、formal_pass_added=0。共通tool SHA256 `8B77C284BC2EA7677D1ACF73A9683FDA20672BCB68BBAB2A9A5D8F79975AA106`。過去P396 r2とは**別時点の証拠**として保存し、旧rootを上書きしていない。

## worker-9独立監査とP399行可視性false-GREEN P2

実装者外worker-9のREAD-ONLY監査は、P398の本物元 `gui.py.main_window` 呼出し・33元ソースWin32固定・元Flet/共通fixture SHAおよびP399 r1実観測の所有/nonce/終了/外向き遮断を照合し、新しいP1や実GUI観測FAILは確認しなかった。一方、P399のcheckerは非ゼロ状態で行IDをテキスト/DOM個数だけ検査し、**正しい行が `hidden` のままでもGREEN** にできるP2としてREQUEST_CHANGESを報告。旧r1の可視画面で実発生した問題ではなく検査器の負例不足。

生成HTMLに同じ正しい失敗ID/件数を置きながら `failed-rows hidden` とした2件のPlaywrightテスト（権限カテゴリ3件と全17復帰の8件）を追加し、旧 `verify_rows()` / `verify_full_reset()` を直接呼んだ結果、**2 failed、8 deselected、`DID NOT RAISE <class 'AssertionError'>`、P399_VISIBILITY_RED_EXIT=1** と再現。最小修正は正例の各 `data-work-id` 行に`expect(row).to_have_count(1)`と`expect(row).to_be_visible()`を必須化し、全件復帰も先頭8 IDを可視で検査。生成元Fletの固定原票・本番データ・製品ソースには変更なし。

最新の関連局所：P394/P396/P399の3pytestファイル合計 **43 passed in 54.42s / P399_P401_FINAL_LOCAL_EXIT=0**。修正後NiceGUI生成GUIは、新root `artifacts/p3-399-nicegui-failed17-id-tab-r2` で1回実行：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_id_category_tab_gui.py artifacts/p3-399-nicegui-failed17-id-tab-r2
# P399_R2_CHECKER_EXIT=0
```

`result.json` SHA256 **`FE3383838A90801961EAA922ADB777B057AFBF1F23D0E207970DBA2975A28621`**、success=true/7steps、formal_pass_added=0、外向きHTTP/WS 0、JS/console0、生成DB不変、source/harness変更なし、正常exit0、強制終了なし、server子孫remaining0、port解放、`lifecycle_verified=true`。最新P399 checker SHA256 **`1017DECC5E56B919541A55D3FB15062EBB0FF9FD63F96C37FF27101262E219AD`**。元P398 GUIは既に自然終了しており再起動していない。P399 r1とr2は異なるchecker SHA/別rootであり、旧r1を今回新候補GREENとして引用しない。

元Fletが8件/頁であるというコード事実の訂正は上記で反映済み。worker-9の修正後独立解消判定は別途受領するまで未確定。全Flet/native GDI視覚、全Q15元逐語期待、頁送り/短縮ID/大件数、元152・479、Stage5と実Cookie/配布/CI/本人受入は未達のまま。

## worker-9 修正後独立最終レビュー

実装者とは別のworker-9がP399行可視性P2補修後のchecker・tests・共有runner、P398固定9原票、P399 r2結果と2枚のPNG・終了lifecycleをREAD-ONLYで再確認し、**LIMITED APPROVE、当該false-GREEN P2はscoped CLOSED、追加P1/P2なし**と判定した。`verify_rows()` は権限3・ID0009で1・検索解除3・タブ復帰3の期待全行について一意な`data-work-id`と`to_be_visible()`を確認し、`verify_full_reset()` は17件のうち先頭8行について同条件を要求。正しいDOM件数/IDがあっても親`hidden`で表示されない故障DOMの2負例が同一ヘルパーを使う点も確認した。

独立workerが実ファイルSHAを照合した最新checkerは`1017DECC5E56B919541A55D3FB15062EBB0FF9FD63F96C37FF27101262E219AD`。保存P399 r2のSHA `FE3383838A90801961EAA922ADB777B057AFBF1F23D0E207970DBA2975A28621`、可視のtab-return 3行および空表示の画像内容とSHA、server-lifecycleの正常閉鎖を確認している。workerは新規GUI/pytest/Gitを実行していないため、43PASSとGUI exit0はprime実施済み結果の独立読戻しであり独立試験実行ではない。

この承認は**生成17件・固定Flet所有GUIに対するP399限定診断器**だけ。原Flet中間queue UIA未保存、Flet nativeとNiceGUI CDP撮影差、原Q15頁送り/短縮ID検索・障害復帰・全152/479・実Cookie/配布/CI/本人正式受入には拡張しない。release_ready=false・正式0/152・Stage5 4FAIL/12UNVERIFIEDを維持。
