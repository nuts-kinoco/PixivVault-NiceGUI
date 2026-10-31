# P3-379 — S08親プロセス外部通信フェンス・S07異常終了時復元P2、元Flet全8タブ証拠の継承

## 1. 開始・所有・既存成果

2026-10-31 JST。branch `phase11/p3-vault-cleanup-race-20261028`、開始HEAD `e105ea50c21e3b3c18c18c6c3e31e90fe077fbc2`。`b104390`、`726cc34`、`e105ea5`、`AGENTS.md`、`docs/P3_370_K14_P2_REMEDIATION_FLET_BOUNDARY_S07_S08_2026-10-31.md`、`docs/P3_375_S07_TXT_CONTENT_S08_FRAMES_WIDTH_FLET_GATES_2026-10-31.md` を照合。staged0、stash空、今回の対象Python/Flet実行プロセス0。別担当の V02/P3-348/shared audit dirtyは変更・stage・killしていない。前回S07 P3-372とS08 P3-373の成功result SHA一致、旧出力を書換えていない。

今回の対象は**担当内限定P2二件**。別モデル/worker/後続独立レビューを起動しない。元DB・原画像・ZIP・Cookie・実アカウントを参照/変更せず、外部HTTP GET/POST0、生成専用rootだけに書く。

## 2. P2 S08：親プロセスの外部socket/DNS監視漏れ

**旧不備**：`tools/check_stage5_s08_frames_next_save.py` の外部通信拒否auditは `--serve` 子のみにあり、親側の `create_services`→`save_explicit_work`→保存ファイル読取り→`services.close` を保護していない。ブラウザー `external_requests=0` では親側で例外を飲み込んだ通信試行は検出できない。

修正前の新規負テストを実行：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_offline_parent_fences.py tests/test_stage5_s07_cleanup_restore.py
# 2 errors during collection: tools.stage5_offline_parent_fence missing
# P2_PRE_FIX_EXIT=2
```

これは修正前の**検査機構がないことによる収集失敗**であり、実際の外部通信を発生させたFAILではない。

`tools/stage5_offline_parent_fence.py` を追加。単一所有の試験プロセス内だけで、`socket.socket.connect`、`connect_ex`、`socket.create_connection`、`socket.getaddrinfo`、`socket.socket.sendto` を可逆にラップする。127/8・::1・localhostのみ許容。外向き試行は**実通信前に拒否**し、呼出し元が `PermissionError` を捕捉しても `attempt_count` で残る。コンテキスト退出時は元の関数を全復元。OS全プロセスの物理的ネットワーク隔離を意味しないので、子側の既存auditとPlaywrightルート遮断も維持。

S08親 `run()` の全期間を `with OfflineNetworkFence()` で包む。GUIサーバ起動前、サービス構築、生成保存、playback、正常shutdown、source/harness SHAとreceipt出力まで親フェンス下に置く。実測receiptへ `parent_outbound_attempts`・試行経路を追加し、**内部捕捉されても非0ならsuccess=false**。

負対照：外部 `203.0.113.55` / `external.invalid` の socket/DNSを、実ネットワークへ送らないよう妨害して各経路で1試行と数える。`create_services` の**親側**へ外向きDNSを故障注入して `PermissionError` を内部捕捉し、実生成GUIとフレーム保存自体は終えた場合も全体FAILを検証。新規root `artifacts/p3-377-s08-caught-parent-negative-*` の結果は `tested=true`、`normal_exit=true`、`parent_outbound_attempts=1`、`success=false`。外向き実通信をさせていない。

通常の新GUI実測：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s08_frames_next_save.py --output artifacts/p3-376-s08-parent-fence-r1
# {"success":true,"steps":4,"failure":null,"formal_pass_added":0,"owned_remaining":0}
# S08_PARENT_FENCE_GUI_EXIT=0
```

結果 `artifacts/p3-376-s08-parent-fence-r1/result.json` SHA256 `08543856a3515aebaa7b4c06d31799fb980bdf26120f1694ea1fd1e617f6a43d`。親側外部試行0、実Cookie/外向きHTTP0、app自然exit0/強制killなし/子孫0/port解放。元生成うごイラ123のフォルダー3フレーム・色/時間・所有ID確認は維持。**旧S08成功証拠と新親フェンス付き成功は区別**する。

## 3. P2 S07：異常終了時のsocket復元飛ばし

**旧不備**：`tools/check_stage5_s07_next_saved_txt.py` の `finally` 内で、`services.close()`→元生成設定DB SHA照合→`result.json` 出力→最後に元socket復元、という順序だった。途中のいずれかが例外なら復元行を通らず、検査プロセスのsocket環境を汚染し得た。

S07も同じ `OfflineNetworkFence` の`with`を**全主処理の外側**に適用し、例外時にもPythonの`__exit__`で元socket/DNSを必ず復元。`finalize_s07_run` は主処理`failure`を上書きせず、`cleanup_errors` に stage / 例外型で `services.close`、`source_sha`、`receipt_write`を別記。通常resultの出力で失敗したら**同じパスに再送せず** `result-write-failure.json` へ限定フォールバック。双方の書込みが失敗しても最外側`with`が復元する。復元・SHA・レシートにエラーがあれば`success=false`。親外向き試行も記録・FAIL。

故障注入はサービスclose例外・元SHA例外・通常receipt書込み例外・通常+fallback両出力例外・主処理失敗とcleanupの併発・内部捕捉された外部DNS試行に適用。実外向き通信なしで、例外前後のsocket/connect_ex/getaddrinfo参照の完全一致を確認。保存TXTの45bytes・BOM付きUTF-8・生成作品501/作者91・本文42文字・receiptバイト長/SHA厳密照合は**一切弱めていない**。

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-376-s07-cleanup-r1
# {"success":true,"steps":3,"failure":null,"formal_pass_added":0}
# S07_CLEANUP_GUI_EXIT=0
```

新結果 `artifacts/p3-376-s07-cleanup-r1/result.json` SHA256 `952dba663e0c7331d6b0069bc861a9fdef0f1143ccd04a5f4e8d3e1fead08c889`。親外向き試行0、主処理失敗なし、cleanup_errors空、TXTの全文検査と元生成設定SHA不変。

## 4. 非依存原S08の追加未測定内容と関連回帰

原台帳`v8-S08-01`（UG:106）は GIF/MP4/frames の既定形式。前回は設定GUIでMP4選択保存、今回はframes選択→生成3コマ実保存を確認済み。今回は`tests/test_stage5_s08_gif_default_content.py`により、別の生成専用tmp rootで既定GIF→通常 `save_explicit_work('123')` を行い、出力GIFを再読取り。フレーム実数3、96×64、順にRGB赤/緑/青、遅延40/90/170ms、作者91/作品123/生成元アーカイブ呼び出しを照合。ネットワークフェンス内で外向き試行0。**GIF実出力の新規内容対照に限定したCPU/生成サービス試験**であり、前回の設定GUIクリックを再実行していない。MP4実出力は依然cv2/codec前提未達で正式PASSにしない。

全対象の修正後回帰：

```powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_offline_parent_fences.py tests/test_stage5_s07_cleanup_restore.py tests/test_stage5_s07_txt_content_guard.py tests/test_stage5_s08_gif_default_content.py tests/test_p3_residuals_fixtures.py tests/test_media_coexistence.py
# 35 passed in 10.45s, P2_FINAL_MEDIA_REGRESSION_EXIT=0
```

## 5. 元Flet全8タブ本体比較：他担当の新たな実証結果を継承

前のP3-365合成外形8タブと、本物の元Flet `gui.py` 実8タブを混同しない。別担当の新 `docs/P3_369_370_REAL_ORIGINAL_GUI_EIGHT_TAB_SAFE_SYNTHETIC_ACCEPTANCE_2026-10-31.md` は、元`gui.py`と依存33ソースの保護コピーを実際に`main_window(FakeDB)`へ通し、全8タブをFlet Native Windowで表示・クリック・PrintWindow取得、正常終了したもの。**今回再起動・再クリックしていない**。

安全性と保存済み証拠の読み取り再照合：

```text
P3_369_370_INTEGRITY_PASS source_copy=33 original_gui_sha=PASS captured_pngs=16
FULL_PARITY_UNVERIFIED same_data=False same_capture=False
FLET_REUSED_EVIDENCE_EXIT=0
```

元Flet32依存＋gui.pyの33個SHAと本番`S:/Projects/PixivVault/gui.py` SHA `731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61cd6acbecd84e939`、Flet/NiceGUI8タブ各画面16PNGのSHAが保存済みレシートと一致。元Flet全8タブの実画面到達は担当内証拠あり。ただし`artifacts/p3-370-nicegui-configured-fullshape-r1/matched-eight-tab-analysis.json` SHA `a006775ea376a06a8aac711d221bcaddd95062c822884fb5e84724f345e660f8` は`same_pixel_dimensions=[1768,882]`でありながら`original_fakeDB_and_NiceGUI_managed_fixture_same_dataset=false`、`same_capture_engine=false`、`full_original_behavior_and_saved_format_parity_pass=false`。

後続実施すべき工程は、元Flet保護コピーの本物の全タブに共通生成作品・サービス状態を供給し、現NiceGUIと**同じ初期tab/同じ実データ/同じUI操作・物理DPI・同じ撮影方式**に揃えた実操作と性能30回/p95比較。元Flet GUIの保護フェンスに新しいワーカー/正規Cookie/実DB接続を追加しない。元Flet側のFakeDB空状態とNiceGUI側の管理画像1件状態を一致扱いしない。今回このフル比較は新規物理実行せず、当該依存範囲のみ**未検証**で維持する。

## 6. 残る正式・独立ゲート

元固定正式152操作、479参照の分母・期待条件・既存gap索引は変更/再生成していない。S07=`v8-S07-01`、S08=`v8-S08-01` は**今回の合成限定受入・担当内局所PASS**であり形式PASS加算0。今回のP2コード修正が独立承認されたとは扱わない。K14/K02/K04/K08/L13等の保存済み成功テストは再実行しない。

Stage5依然 **4 FAIL / 12 UNVERIFIED**、正式 **0/152 PASS、479 refs**、release-ready=false。実Cookie・認証状態・原DB/画像/ZIP全bytes保全と保存先owner、実API、原Flet本体同条件比較、全UI/UX、CI、配布exe、独立P1/P2レビュー・本人最終受入はすべて別の未完条件。未承認仕様/フロー変更や外部通信、main統合、pushなし。所有差分だけ作業ブランチへ選択コミットする。
