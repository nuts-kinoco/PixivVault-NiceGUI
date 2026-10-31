# P3-404 — 元Flet本物の失敗リスト17件3頁とNiceGUIの実操作比較

2026-10-31 JST。開始HEAD `8d72493db295a1be21a047a1b227a81d75391e6a`、branch `phase11/p3-vault-cleanup-race-20261028`。固定Q15（検索変更時は先頭頁、頁送りと結果の一貫性）の未実測条件を、元Flet `gui.py.main_window` の実ウィンドウと現在NiceGUI `FailedRecordsView` で別々に安全な生成17件を使って照合。P389/390、P392/393、P395/396、P398/399の成功済み操作は繰り返さず、この新しいページ移動だけを加えた。

原Flet本番ソース、元DB/WAL/SHM/画像/ZIP/Cookie、実アカウント・外向きPixiv APIは変更も使用もしない。元Fletの再試行/削除/CSV/全件クリアは押していない。生成テストrootは `artifacts/` の新ディレクトリのみで、証拠画像・秘密値やローカルユーザー情報をGitへ追跡しない。

## P3-402 本物のFlet gui.pyと正規Desktop実クリック

起動：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'C:\Users\nat\miniconda3\envs\stenv-flet1\python.exe' -X utf8 -B tools/check_stage5_original_failed17_native.py artifacts/p3-402-real-flet-failed17-pagination-r1
# P402_FLET_EXIT=0
```

元の保護コピー33ソースをWin32 `FILE_SHARE_READ`で実行中固定し、本物の `gui.py.main_window` 全8タブを構築した生成FakeDBから元失敗一覧に共通17件を渡す。Desktopで所有PixivVault Flet実ウィンドウ **HWND48831320**、本体 **PID129912** を確認。元コード `source-copy/gui.py:4090,4356-4364` は8件/頁で17件なら3頁。実クリックの結果は次の通り：

| 実Flet操作 | 実UIAの観測 | 保存原票SHA256 |
|---|---|---|
| 初期1頁 | 0001～0008を表示、0009は表示なし | `0DBCC979CE206329B5D2226706F38BB18A2D824CF365620E346C6B801F0640C6` |
| 次へ→2頁 | 0009～0016、頁表示「2 / 3」 | `9C52695DE3BEA8CA765F7D707DD1BA83EBEAF489103778CDF904414BAF5FF0DD` |
| 次へ→3頁 | 0017のみ、頁表示「3 / 3」 | `FFB3BD00CCF770C686EF3DEAC9A882559753C04D27FCB59EF6FCF139B3E34FD4` |
| 前へ→2頁 | 0009～0016、頁表示「2 / 3」 | `3228AD8BE2A84DFFEDD2B86B4E8707E9FFC9ADCC71E033432097F6BFD42A3784` |
| 2頁でID検索 `failure-` へ変更 | 0001～0008の先頭頁へ復帰 | `7AE2B1D7AAEA389CBEC16A13291CA13FA92658A729D6678DCB66332E0C83122C` |

上表は同一ウィンドウのframe単調増加（73→74→75→77→79）と対象外ID不在を含む。Flet表示の2頁・3頁ラベルは保存UIAに含まれる。**1頁の頁ラベル「1 / 3」はDesktop `get_window_state(query='1 / 3')` で別途直接確認したが、最大100要素の生UIAと同一フレームには収録できていない**。保存した最後の原票に `page_label_queried_separately` の診断補助文字列を付けたが、これは自己申告フィールドであり、独立署名された同一フレーム証明と扱わない。Fletの元画面サイズ/DPIとNiceGUI CDPの撮影エンジンの同一性も未検証。

P402 ready SHA256 `D6B2AD68438136CDF4B17316D498616752CCE54E2B0EA3DD0453F294412CEE36`、終了source guard SHA256 `A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6`。本物のタイトルバーから通常終了exit0、元33ソース不変、本番 `gui.py` SHA256 `731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939`、関連PID残留0。生成fixture17件・形式／タイトル／IDは既存P389/390/392/398と同一正本。

## P3-403 NiceGUI生成専用実ブラウザ

新 `tools/check_stage5_failed17_pagination_gui.py` はP402のready/guard+5 UIA、P398とそれ以前の本物Flet原票を固定SHA・固定HWND/PID・期待ID・頁ラベル・フレーム順で照合してから、既存の共通owner/network/lifecycle検査器に新しい頁操作だけを渡す。UIに表示された `failed-record` は対象IDの順序・正確な件数・行の可視性と非対象ID不在を確認。製品の検索・頁送りボタンの実クリック、disabled状態、最後頁、検索変更による頁リセットを検査する。第三者署名のFlet原票trust rootではなく、固定済みP402の限定比較である。

新局所テスト `tests/test_stage5_failed17_pagination_gui.py` は原票改ざん（位相/本文/owner/全HWND）を拒否し、頁番号・行IDが正しくても親が非表示ならfailする3つのPlaywright DOM負例を含む。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_failed17_pagination_gui.py tests/test_stage5_failed17_id_category_tab_gui.py
# 19 passed in 29.49s / P403_LOCAL_EXIT=0
```

新GUI起動：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_pagination_gui.py artifacts/p3-403-nicegui-failed17-pagination-r1
# P403_GUI_EXIT=0
```

`artifacts/p3-403-nicegui-failed17-pagination-r1/result.json` SHA256 **`EDC09BE241C630E709D1B580D53F09C46E446A64E682FCBDB6ECBA2895795929`**。実NiceGUIは初期17件1/3頁→次2/3（0009～0016）→次3/3（0017のみ・次無効）→前2/3→検索 `failure-` に変更で1/3へ復帰、**5 steps** success=true。fixed生成fixture fingerprintは元Flet/NiceGUIとも `711C0BD7F743688AB34F3DBB83F2B4E0CB3319C4266A29F9F1419A7B6AD41AE0`。新checker SHA256 `9934CABD3283E97F67487B6B65DB04BDBDA5762062151D52E96D66164AB210C7`。

正常終了exit0、forced_cleanup=false、server-owned remaining0、port解放、`lifecycle_verified=true`、ブラウザ外向きHTTP/WS 0、JS/console error 0、元生成DB/source/harness不変、`formal_pass_added=0`。NiceGUIはCSS1770×884/DPR1.75のCDP撮影で、P402元Flet Desktopネイティブ撮影と完全同条件という判定はしない。今回の機能限定成功を全Flet/native parity・原逐語期待・配布/CI/性能/実CookieのPASSへ変換しない。

## 独立レビュー・正式受入の扱い

worker-9へ最新checker/test/保存証拠のREAD-ONLY独立レビューを依頼済み。結果とP1/P2指摘対応は正式に回収してから判定。原152件・479原参照の元ID/期待/分母は変更していない。正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`。今回のFlet画面は再試行/削除/CSV/実アカウントへ手を触れず通常終了、元原本保全。push禁止・main統合禁止。他担当dirtyは編集・stageしない。

## worker-9 独立限定レビュー結果

実装者とは別のworker-9がP402の固定7原票の実SHAを読戻して全件照合し、保護された本物の元 `gui.py` 実起動、元33ソース・生成fixture、現P403のchecker/test/保存GUIと終了レシートをREAD-ONLY独立監査。**LIMITED APPROVE（今回の17件3頁・検索リセットという限定シナリオのみ）、新P1/P2なし**。元Flet原票のframe73→74→75→77→79、同HWNDと各頁に必要な行ID、2/3・3/3・2/3の保存頁ラベル一致を確認した。初期および検索後の1/3頁ラベルがP402 UIAに収録されていないという制約も明示的に維持。

NiceGUIの新checkerでは `visible_page()` が各段階の17/17ラベル、n/3、正確な行数と順序・ID、行の実可視性、空状態非表示、他ID不在を同時に確認し、末頁次ボタン無効/頁送り/検索変更の状態も確認。独立workerはP403 r1のresult SHA `EDC09BE241C630E709D1B580D53F09C46E446A64E682FCBDB6ECBA2895795929`、2画面PNGのSHAと内容、生成DB/lifecycle所有PID・正常終了・外向き0の整合を確認している。局所19PASSは実装側の原結果を受領したもので、worker自身が試験を再実行したわけではない。

限定承認は正式Q15全範囲、原本人証の独立署名、同一native GDI撮影、全8タブ・実API・配布/CI/本人最終受入には拡張しない。正式0/152、479原参照、Stage5 4FAIL/12UNVERIFIED、`release_ready=false`のまま。
