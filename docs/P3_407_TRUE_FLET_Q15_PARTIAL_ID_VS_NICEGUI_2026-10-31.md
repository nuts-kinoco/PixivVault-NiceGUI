# P3-407 — 本物の元FletとNiceGUIのID部分一致・0件・解除を生成17件で比較

2026-10-31 JST、開始HEAD `663d043ab2651678c0e6beea9b8c19dc00494f60`、branch `phase11/p3-vault-cleanup-race-20261028`。原Q15にはwork_id文字列の**部分一致**検索が含まれる。以前のP398/P399はID `failure-0009` という全文条件を確認しており、本件は新しい `0009`、`0017`、`0000` の短い検索文字列を元Fletの本物のGUIとNiceGUIで実操作したもの。成功済みP398/P399やP402/P403を単に再実行したのではない。

原Flet本番GUI、元DB/WAL/SHM/画像/ZIP/Cookie/設定、実Pixivアカウントと外部APIを変更・利用しない。すべて同じ新規生成17件を元Flet `gui.py.main_window` の元失敗リストとNiceGUI製品 `FailedRecordsView` に渡す。元Flet再試行/削除/CSV/全件クリアは実行していない。

## P405 本物Fletの実GUI/原票

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'C:\Users\nat\miniconda3\envs\stenv-flet1\python.exe' -X utf8 -B tools/check_stage5_original_failed17_native.py artifacts/p3-405-real-flet-failed17-partial-id-r1
# P405_FLET_EXIT=0
```

元ソース33件は実行中Win32 `FILE_SHARE_READ`で固定し、本物 `gui.py.main_window` の全8タブを構築。元データ・元ワーカーは切断、合成FakeDBの `get_failed_jobs()` に架空17件を接続した。実Desktopによる所有実ウィンドウ **HWND43909378**、原GUIプロセス **PID133152** から、各操作後のアクセシビリティ原票を生成専用root `artifacts/p3-405-real-flet-failed17-partial-id-r1` に保存。

| 実操作 | 実表示観測 | raw UIA SHA256 |
|---|---|---|
| 初期 | 17件、先頭0001～0008 | `4EB23695B33885C80108E534442D84E23BC08DA324CC6AB1B378A50CE7AFA7B9` |
| 検索 `0009` | 0009のみ、1件 | `2714D57D9017724B854644B667536ED9C4466F198C534DF1B9893C5B1BEF5F60` |
| 検索 `0017` | 0017のみ、1件、タイトル `=SUM(1,2)` | `0581526A88B515E500BE85156EE7555E70F017192B72665334387E07506E54B6` |
| 検索 `0000` | 一致なし・再試行できるものなし・検索を消す導線 | `CE4439D7C32C6EE48B0988F77A709ED7E7E08A8F820762E8FA271F77250196F1` |
| 検索を消す | 17件、先頭0001～0008へ復帰 | `C6F54AA4FF9BA61A24A0CA5CB03D1A65137EA535A3D5E4D0E9F10D46AE7C04F9` |

同HWNDとframe80→82→83→84→85を順に観測。P405 ready SHA256 `4E7F86E7D5F7B19E090DADD9F52D33B91807DF0D2946C87F7164C21E1F63423F`、終了guard SHA256 `A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6`。タイトルバー実閉鎖でexit0、元33ソース不変、原本 `gui.py` SHA256 `731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939` 不変、関連PID0。

**観測の限界：** Fletの生UIA上の検索 `Edit` テキストはツールのアクセシビリティ出力から省略されている。文字列 `0009` 等を実入力してから結果を観測したが、固定UIAファイルだけではキーボード送信全文の独立証明にならない。結果の実表示/原票SHAと、ツール呼出し時の入力操作は別の根拠として区別する。

## P406 NiceGUI製品実GUI

新 `tools/check_stage5_failed17_partial_id_gui.py` はP405元Fletのready/guard+5 UIAを固定SHA・固定HWND/PID・必要/排除文字列・frame順序に結合し、先行P402/P398の元GUI原票と元33ソース/同一fixtureを検証してから、既存のowner付き・外部通信遮断のブラウザハーネスで**新条件だけ**実操作する。検索・表示の合致は件数ラベルだけでなく、カテゴリ選択、頁、行の実数、唯一のdata-work-id、行の可視性、空状態の可視/非可視、検索欄の値で検査する。

新 `tests/test_stage5_failed17_partial_id_gui.py` は正しい原票、改ざん4負例/統一偽HWND拒否、正しい行IDで親が非表示の生成DOM負例を含む。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_failed17_partial_id_gui.py tests/test_stage5_failed17_pagination_gui.py
# 16 passed in 24.02s / P406_LOCAL_EXIT=0
```

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_partial_id_gui.py artifacts/p3-406-nicegui-failed17-partial-id-r1
# P406_GUI_EXIT=0
```

新結果 `artifacts/p3-406-nicegui-failed17-partial-id-r1/result.json` SHA256 **`95741396B3A84B9CDC7213D45626C0151772A069FB9F30451421956E0322BA0E`**。NiceGUI初期17→検索0009で1→0017で1（title `=SUM(1,2)` を表示文字列として確認）→0000で0・専用empty表示→検索解除で17の**5 GUI steps、success=true、failure=null、formal_pass_added=0**。元Flet/NiceGUIの生成fixture fingerprintは `711C0BD7F743688AB34F3DBB83F2B4E0CB3319C4266A29F9F1419A7B6AD41AE0` で一致。

終了レシートは正常exit0、強制cleanupなし、server-owned子孫remaining0、port解放、`lifecycle_verified=true`、外部HTTP/WS 0、JS/console error0、生成DB/source/harness変更なし。最新checker SHA256 `33172ECB665FE1457BAF86F2DD8074ED95D9D0E58B8F4F4A81661B87185B0FB3`。Flet DesktopとNiceGUI CDPは同一native撮影ではない。元FletのUIAは元ソース実行から得た限定証拠だが、独立署名による全製品trust rootではない。

## 未達ゲート・独立レビュー

今回の新局所結果についてworker-9へREAD-ONLY独立監査を依頼済み。新P1/P2指摘と対処があればRED→修正→GREEN→再レビューに従う。上記限定Q15追加を全Q15原逐語expectedの正式PASSへ計上しない。元152・479の分母不変、正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`。真の失敗記録0件と検索で0件の区別、エラー復帰/更新/再起動、画面幅・53px行、全8タブ同条件GDI撮影、実Cookie専用管理root・ZIP/Vault、最新Windows配布/CI/性能/本人受入はまだ未達。push/main統合禁止、他担当dirtyは保全。

## 独立worker-9のP2と修正後実GUI

worker-9の初回READ-ONLY独立監査は本物Flet生成17件・固定UIA/元33ソース/同一fixture・P406 r1の所有/通信/正常終了について限定事実を確認した一方、**checkerのタイトル子要素のみ非表示にしても合格するP2**を発見し `REQUEST_CHANGES`。製品側 `failed_records_view.py:256` ではタイトルが独立した `.pv-failure-title` であり、行本体をvisibleとしてもタイトル子だけhiddenにすると、旧checkerのrow `to_contain_text('=SUM(1,2)')` はtextContentを読んで通ってしまう。保存r1の実画面でその表示不良が発生したという指摘ではない。

- 故障DOM負例 `test_hidden_formula_title_inside_visible_record_must_fail` を同じ実検査ヘルパーから実行する形で追加し、正しい検索0017・件数1/17・行work ID0017・行visible・metadata可視、**title子だけhidden**で旧コードが拒否できず、`1 failed, 7 deselected / DID NOT RAISE <class 'AssertionError'> / P406_HIDDEN_TITLE_RED_EXIT=1` を実確認。
- 修正後は `assert_formula_title_visible()` で単一行内 `.pv-failure-title` のcount1、`to_be_visible()`、完全な `=SUM(1,2)（ID: failure-0017）` を同時要求し、NiceGUI実GUI `search_steps()` の0017段階から利用。正しいタイトル表示DOMがPASSする正例も追加した。
- 修正後の新局所関連は `tests/test_stage5_failed17_partial_id_gui.py` と `tests/test_stage5_failed17_pagination_gui.py` の合計 **18 passed in 30.24s / exit0**。旧16PASSは修正前候補の履歴として保存。
- 修正後の新NiceGUI実GUIを、旧r1 rootを上書きせず `artifacts/p3-406-nicegui-failed17-partial-id-r2` で1回起動。**success=true、5steps、checker exit0、formal_pass_added=0、通常終了exit0、強制cleanupなし、owned server remaining0、port解放、`lifecycle_verified=true`、ブラウザ外向きHTTP/WS0・JS/console0、元生成DB/source/harness不変**。
- 新r2 result SHA256 **`885B1CFCC05AC2FD451F44D64549BDBCE37D516E2BE64B67A574F04E2B3F335F`**。修正後checker SHA256 **`1B936373D0CD13A30E61FFA9E076AB75EB3D491948AE3558C805696844FC860A`**。r1とr2は異なるchecker時点の別root・別証拠。

worker-9の最新r2差分独立再レビュー結果は未回収。修正後の限定P2を独立CLOSEDと扱うのはその判定後。全製品・元逐語expected・正式152/479・Stage5/配布・本人受入へ広げない。

## worker-9 修正後独立限定最終判定

worker-9が最新checker/testとP405元Flet固定7原票・P406 r2のresult/lifecycle/2画像をREAD-ONLY再監査し、**LIMITED APPROVE（当該生成17件の部分ID検索と、タイトル非表示false-GREEN P2に限りscoped CLOSED）、追加P1/P2なし**と判定。単一行の可視性と検索値/ID/件数を確認した後、`.pv-failure-title` それ自体が1要素・visible・文字列完全一致である必要があり、隠れたタイトル子のtextContentだけで通る以前の反例を拒否することを確認した。旧REQUEST_CHANGESはこの限定改修に関して解消。worker自身はpytest/GUIを再実行しておらず、元結果の独立読戻しを行った。

承認はこの固定条件と誤合格穴の解消に限る。実Flet Edit送信値の原票欠落、第三者署名の完全所有証明、両UIの同native撮影、真の元失敗0件、原152逐語expected/479参照、Stage5/実Cookie/Win配布・CI/本人最終受入は依然未達。正式0/152・release_ready=falseを維持する。
