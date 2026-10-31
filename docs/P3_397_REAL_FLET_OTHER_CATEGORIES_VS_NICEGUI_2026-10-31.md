# P3-397 — 失敗リスト残り5カテゴリの本物FletとNiceGUI実GUI限定比較

2026-10-31 JST。開始HEAD `bf78e2722cb64f14dbd5dd916ce0d53f804f14d8`、branch `phase11/p3-vault-cleanup-race-20261028`。P3-392/393で測った「通信」カテゴリとtitle検索ANDを繰り返さず、既存の正規17件生成fixtureから**まだ未測定だった5カテゴリの実クリック**と全件復帰を独立の新rootで検査。元Fletの本物の `gui.py.main_window` を使い、模造UI、FakePage、元DB/Cookie、原本作品、実API、外部通信を用いない。実行・証拠は生成専用 `artifacts/` に保存、追跡・公開しない。

## P3-395 元Flet本物の実GUI操作

起動：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'C:\Users\nat\miniconda3\envs\stenv-flet1\python.exe' -X utf8 -B tools/check_stage5_original_failed17_native.py artifacts/p3-395-real-flet-other-categories-r1
```

- 既存33個の元ソース保護コピーをWin32 `FILE_SHARE_READ`ハンドルで起動中固定。元Flet本番の `gui.py` と33ソースのSHA不変を検査。FakeDBが返すのは元製品と切り離された架空17件のみ。
- 本物のFlet所有GUI：起動PID **124288**、Desktop所有PixivVault window HWND **83303156**。実「失敗リスト」をクリック後、チップを順に実クリックし、各回の `Chat_On_Steroids_Desktop.get_window_state` 生UIA・frameを別ファイル保存。再試行、削除、CSV、全件クリアは実行していない。
- 画面上の期待レコードと非対象レコード不在を各状態で確認した。UIAはFlet GUI直接の実観測だが、独立署名済み全製品trust rootではない。

| 実操作 | 実UIA frame | 期待/観測（全て一致） | raw UIA SHA256 |
|---|---|---|---|
| 見つからない | frame-51 | 3件: 0002,0008,0014 | `4954B6AE5114288D1DFFC66B60D490087695515F04D6F1B3860185978F295514` |
| 権限・年齢制限 | frame-52 | 3件: 0003,0009,0015 | `19F16C7FCA425F8196D32A653CD06EF84EA708DFEBCFDB14902E5E0FCE420510` |
| 保存先 | frame-53 | 3件: 0004,0010,0016 | `2AA111A07426B7A07A4D9315E90B34F68FDDA9E6946EBC8088883CA5024E3944` |
| 形式 | frame-54 | 3件: 0005,0011,0017 | `F785DAF2A519B13748866702DF6FA020A36E1115B6B8DBE6F4464542EBC355E4` |
| その他 | frame-55 | 2件: 0006,0012 | `8C3B83C37B905A2E4E63B2E53592EC400C4FCABBF0F8120C01B4B909C8D08983` |
| すべてへ復帰 | frame-56 | 全17件 | `3D8249EA1B8B7C3E09176AE68A658E34A2F2FCE7CEE71182230BFAF6B398C8B7` |

元Flet ready `actual-original-full-gui-ready.json` SHA256 `3168B09CEF5FEE7565A08E3511CDB7F8E8ECDB99B4714844CE9FE9DC1CF05C88`、終了guard `generated-failed-17-source-guard.json` SHA256 `A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6`。元Flet通常終了 **exit0**、元33ソース不変、関連PID残存0、元本番 `gui.py` SHA256 `731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939` で不変。

## P3-396 NiceGUI生成専用実GUI

新規 `tools/check_stage5_failed17_other_categories_gui.py` は既存のP3-389/P392本物Flet固定原票と新P3-395 8原票（ready、guard、6 UIA）を起動前に固定SHA・固定HWND・frame単調順・件数/IDで検査する。独立したFlet再起動はせず、現在のNiceGUI実 `FailedRecordsView` に同じgenerated17を渡し、CSS1770×884/DPR1.75/物理3098×1547で全5カテゴリと「すべて」へ戻すPlaywright実GUI操作。各カテゴリは件数と対象ID全件、非対象IDの非表示を検査。外向きHTTP/WS遮断、所有PID/子孫/終了、生成DB SHA、製品/ハーネスSHA前後、server-lifecycle票も検査する。

初回起動コマンドでは `PYTHONPATH=src` 時に `ModuleNotFoundError: No module named 'tools'` が発生し、元GUI/NiceGUIプロセス起動前に停止。実行パス挿入を修正して、新root `artifacts/p3-396-nicegui-failed17-other-categories-r2` で正規実GUIを一度実行（旧r1の成功を偽装しない）。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_other_categories_gui.py artifacts/p3-396-nicegui-failed17-other-categories-r2
# P396_R2_CHECKER_EXIT=0
```

- result.json SHA256 **`AFA69749CD19F982F6B2CB0D6EEC6052EC38A4A11913FD67CA09075EA9818BFA`**。
- **success=true、7実GUIステップ、failure=null、formal_pass_added=0**。
- 正常終了exit0、強制cleanupなし、owned server-tree remaining0、port解放、`lifecycle_verified=true`、ブラウザ外部HTTP/WS 0、JS/console errors0、生成DB不変、source/harness変更なし。
- Native Flet PrintWindowとNiceGUI CDPは**撮影方式が異なる**。原Fletの5カテゴリごとのGDI画像・NiceGUI同一native GDI、データ更新・例外復帰・検索AND全種類・全元逐語expected・他画面幅は未検証。
- 新規の原票負例を含む `tests/test_stage5_failed17_other_categories_gui.py` とP394 testの合計は **24 passed in 7.15s / exit0**（P394全新P2修正込み）。単体試験と実GUIは別の証拠。

## 固定原要求との対応・保留

Q14「失敗理由カテゴリで絞る」の従来未測定5分類と全カテゴリ解除の限定実画面証拠を追加。これまでの通信カテゴリ＋Q15タイトルANDはP3-392/393 r3参照。Q15の先頭頁復帰・ページ送り、ID/タイトル交差競合、復帰、ログ・CSV・一括実処理、0件空DB区別などは別未達。正式152の分母・逐語expectedと479原参照は変えていない。今回新規の正式PASSは **0**、全体正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`。P1/P2の独立解消レビューと、元Flet全8タブ同一native比較、実Cookie境界、ZIP/Vault、CI/配布、本人受入の達成まではmain統合・完成認定しない。pushなし。他担当のdirtyには触れていない。
