# P3-375 — S07本文P2修正、S08次回保存、設定UI幅実測、Flet正式比較ゲート

2026-10-31 JST。対象 `S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028`、作業branch `phase11/p3-vault-cleanup-race-20261028`。開始HEAD `726cc34278113ece96ae62cb00f936c05291c1c9`。前回コミット `b104390`（K14 P2 checkerとS07/S08設定GUI）・`726cc34`（S07次回TXT生成保存）を確認した。staged0、stash空、関連所有実行PIDなし。その他のdirtyはV02/P3-348および別担当成果として編集・stage・killしない。

## 1. S07 P2：本文なしでも誤PASSの再現→checker修正→新局所実行

指摘は `tools/check_stage5_s07_next_saved_txt.py:84–101` の旧判定。ジョブdone、`saved_entries()` の `TXT` と `.txt` 実在・`.epub` 不在を確かめるだけで、TXTが空・誤本文・別作品でも通り得た。前回保存済み `artifacts/p3-371-s07-next-txt-generated-r3/result.json` SHA256 `7be2472b6f027b40bdad60d068ac4448ebd18d23f78cb02cb4efd72debbf3991` は改変しない。あれは**形式・ファイル存在までの限定成功**であり、本文検証済みと扱わない。

新 `tests/test_stage5_s07_txt_content_guard.py` は、旧predicateを実装して「総数/ファイル/拡張子が正常のまま内容を見逃す」ことを故障注入10件＋正常対照1件で記録した。故障ケース：空本文、非空誤本文、別作品502の本文、壊れたUTF-8、BOM欠落、receipt work_id 502、author_id 92、誤タイトル、章ID 502、正常ファイルの改変＋旧receipt SHA。実元データを使わず`tmp_path`だけ。

赤テスト：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_s07_txt_content_guard.py
# ImportError: cannot import name 'EXPECTED_TXT_501'
# S07_RED_GUARD_EXIT=2（修正前の新ガード不在／収集失敗であり、製品FAILではない）
```

修正対象は専用チェッカー**のみ**。既知の生成入力 `FixtureReader.get_novel_payload('501','91')` の `Generated fixture text.[newpage]Second page.` を確認し、保存されるpractical TXTの実仕様に従う**BOM付きUTF-8**と変換後全文 `Generated fixture text.\n\n---\n\nSecond page.` を厳密比較。ファイル非空、strict `utf-8-sig` 復号、完全な文字列/bytes、出力ファイル名、生成作品501・作者91・タイトル・章IDのreceipt、receipt内のbytes/sha256と実ファイルSHA、TXT1本/EPUBなしをチェック。`verify_generated_txt_501` からbody SHAと文字数を構造化返却する。

局所結果：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_s07_txt_content_guard.py
# 11 passed in 0.93s, S07_CONTENT_GUARDS_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-372-s07-content-r1
# {"success":true,"steps":3,"failure":null,"formal_pass_added":0}
# S07_CONTENT_R1_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_s07_txt_content_guard.py tests/test_p3_residuals_fixtures.py tests/test_novel_save_presentation.py tests/test_novel_practical_export.py
# 34 passed in 4.62s, S07_RELATED_REGRESSION_EXIT=0
```

新レシート `artifacts/p3-372-s07-content-r1/result.json` SHA256 `954b2cf3f7a12f8eb92c54e8d2c5a7ae203709a51e4ef4d6cc37c2f8099968d7`。本文45bytes、BOM除去後42文字、work_id 501、author_id 91、receiptとのbytes・SHA一致、`text_matches_fixture=true`。元GUIで選んだ `txt` を保持した**前回生成専用設定DB**のみ読取コピーし、別rootに新実ジョブ/実TXTを保存。元DB・Cookie・画像・ZIP・設定は変更なし。

**正式v8結合：S07 = v8-S07-01（UG:105）。** 今回はGUI選択後の後続合成TXT本文・receiptの整合を確認したが、EPUB/BOTHすべての次回保存の同等性と元Flet製品実GUIの完全比較は未検証。形式PASS加算0。指摘P2のchecker/担当内合成範囲は修復、独立解消レビューは未開始。

## 2. S08 新規GUI「フォルダー」選択→別製品サービスの次回実保存→内容照合

以前P3-369は `ugoira_save_format=mp4` のGUI選択と専用DB永続化まで成功していたが、現環境の `cv2` 不在で実MP4出力未測定。代わりに別の未測定原選択肢「フォルダー」を**新しい専用生成root**で実選択、MP4受入成功を仮定しない。

`tools/check_stage5_s08_frames_next_save.py` は、オフラインNiceGUI実GUIで `うごイラ形式→フォルダー（フレームとJSON）` をクリックし、実保存ボタンを押下。専用設定DBがrevision0→1、`ugoira_save_format=folder` を保持。NiceGUIのown serverを通常exit0で終了後、同じ生成rootから製品 `create_services(FastAPI(),root)` を新規生成し、通常 `save_explicit_work('123')` へ渡す。完成後、保存済みugoiraライブラリの実entry/work123/author91/output=framesを確認し、製品実保存からの `playback` で**3枚のPNG実読取り・デコード**を行う。各フレーム96×64、ピクセルRGBは赤(255,0,0)→緑(0,128,0)→青(0,0,255)、元遅延40/90/170ms、合計300msに一致。

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s08_frames_next_save.py --output artifacts/p3-373-s08-frames-gui-next-save-r1
# {"success":true,"steps":4,"failure":null,"formal_pass_added":0,"owned_remaining":0}
# S08_FRAMES_GUI_R1_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_s07_txt_content_guard.py tests/test_p3_residuals_fixtures.py tests/test_novel_save_presentation.py tests/test_novel_practical_export.py tests/test_media_coexistence.py
# 39 passed in 5.98s, S07_S08_MEDIA_REGRESSION_EXIT=0
```

新レシート `artifacts/p3-373-s08-frames-gui-next-save-r1/result.json` SHA256 `13b7d976434267b1aafdaa9e5ea95a036495e29419c3562938d397b9dba1e51e`。app自然exit0、強制cleanupなし、owner子孫0、port解放、外部HTTP0、JS error0、source/harness SHA変化なし。フレーム別SHAはJSON保存。

**正式v8結合：S08 = v8-S08-01（UG:106）。** 今回はフォルダー選択のGUI→設定保存→実保存の元3フレーム内容とタイミングの限定確認。GIF/MP4の全形式・Flet全製品・実素材/codec条件は未検証であり、特にMP4は `mp4_environment_unverified` のまま。形式PASS加算0。

## 3. S07/S08 設定画面の画面幅別実UI

`tools/check_stage5_preferences_width_gui.py`。以前の設定保存成功を再試行せず、新しい生成画面で 720×900 と 1280×720 CSS pixel / DPR1、設定ダイアログを実クリック。両保存形式コントロールをそれぞれスクロールして表示/有効確認。画面外の横方向にはみ出さないことを、実DOM `getBoundingClientRect`、`scrollWidth/clientWidth` で測定。

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_preferences_width_gui.py --output artifacts/p3-374-settings-width-r1
# {"success":true,"widths":2,"failure":null,"owned_remaining":0,"formal_pass_added":0}
# SETTINGS_WIDTH_R1_EXIT=0
```

`artifacts/p3-374-settings-width-r1/result.json` SHA256 `eb28eb5b00f5c0ba28ab9e8e3001220423c7a6602fc0d920d2f47043410bfbcc`。720幅ではcard左右24–696px、1280幅では304–976px。両方card scrollWidth/clientWidth=672/672px。縦は内部スクロール（2300/810と2300/648px）。自然shutdown、強制停止なし、子孫0、port解放、外部通信0/JS例外0。**設定ダイアログに限定した実測**であり、元Fletや全製品の全タブ・全画面幅でのUX合格ではない。

## 4. P3-365・元Flet実製品フル比較の安全境界

前作業は**再実行していない**。既存 `artifacts/p3-365-matched-shell-r1/result.json` SHA256 `48e9dd4bcd85234c59cc58585e180c761943ef6d3f24f0fd46947a7a98802667` とFlet `flet-close-receipt.json` SHA256 `859748bddba2eab1da5e12d7a1d288eb7be560260909d70ed90ba3103e94bc82` を再照合。Flet合成全外形は正常exit0、NiceGUIの現全シェル合成renderも成功。ただしFletではランキング以外の7タブは**実元製品ではなくプレースホルダー**、撮影はPrintWindowとCDPという異なる経路。両画面1784×921、Flet実DPI96、NiceGUI DPR1、初期状態も元Flet `TAB_INITIAL=unified` と合成ランキング初期で不一致。

元Flet `S:/Projects/PixivVault/gui.py` のSHA256 `731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61cd6acbecd84e939` 不変。実 `gui.py` は `Database()` 自動open、`discovery_tab/library_tab/recommend_tab/unified_download_tab` 等の多数の原依存、main_window終端に推論worker起動とCookie検査スレッド、Cookie自動抽出更新経路を持つ。既存7モジュールfixed隔離ではそれら全ての実副作用/元ファイルアクセスを封鎖できない。元Flet実製品 `gui.py` を無フェンスで起動せず、元DB・Cookie・画像/ZIP・実workerへの全権限を遮断した全タブ実コード同一生成fixtureハーネスが完成するまでは**フル同条件Flet↔NiceGUI実GUI比較未実施**、正式PASSなし。この阻害に依存する工程のみ保留。全機能/操作・スクロール/Viewer/焦点・同一撮影backend・同一DPI/初期タブ・p95・配布exeは未達。

操作フロー変更や全画面overlay採用などの未承認案は実装していない。推奨するのは、**試験専用コピーにだけ**原全タブimportの明示allowlist、元本番root denylist、DB/画像/Cookie/workerの全注入先を照合し、実元コードの機能を無効化/模倣せず合成入力へ置換できる境界を確立してから同条件物理比較すること。できない原操作は未実測のまま残す。

## 5. 独立ゲート・所有権・残件

- K14に対する前回レビュアー指摘の修正確認は**静的読取のみ**（実行なし）。実行によるK14 P3-368成功は別の既存担当内証拠。K02/K04/K08/L13の既存成功も今回再実行していない。
- 元固定152操作・479参照の分母を変更せず、索引 `artifacts/p3-363-formal152-gap-index-r1/all-152-original-id-gaps.json` を再生成していない。新証拠はS07/S08各 `v8-S07-01/v8-S08-01` に限定結合。
- 実Cookie依存の認証・通信・保存先ownership・原DB全bytes不変・実Pixiv接続は依然必須ゲートで、現時点の本人許可/元データ保全境界では実行しない。独立P1/P2解消再判定・本人受入・CI・配布・main統合も未完了。
- 最終読取：Stage5 **4 FAIL / 12 UNVERIFIED**、正式 **0 PASS / 152 INCOMPLETE・479 refs**、release-ready=false。担当内合成GUI・生成実保存と正式完成を混同しない。
- 選択コミットには本docと今回作成/変更した checker/tests のみ含める。`artifacts/` はgitignore対象なので実証跡は元の生成rootにSHA付きで保全し、他担当のdirtyをstageしない。pushなし。
