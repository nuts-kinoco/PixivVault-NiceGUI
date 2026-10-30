# P3-371 — S07 設定GUIから次の実TXT保存への非依存接続を確認

2026-10-31 JST。P3-370のK14修復とS07/S08選択・保存GUI実測の後続。HEAD開始 `b104390`。元DB/画像/ZIP/Cookieや外部Pixiv通信、別担当差分を扱わず、専用生成rootに限定している。

## 根拠と元条件

- 固定元台帳：`docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json` SHA256 `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4`。S07 = `v8-S07-01`（UG:105、EPUB/TXT/BOTHの既定形式）。S08 = `v8-S08-01`（UG:106、GIF/MP4/frames）。
- 既存P3-369保存済みGUI: `artifacts/p3-369-s07-s08-prefs-r1/result.json` SHA256 `2784d228acdb2e087dc6dc5914ca7d87014420e04d1f94a41788643862b58b5a`。専用DB `generated-data/preferences/preferences.sqlite3` の新規実クリックによる `novel_save_format=txt` / `ugoira_save_format=mp4`、revision 1、異なるブラウザclientでも保持。

## 新規工程（既成功GUI操作の再実行なし）

`tools/check_stage5_s07_next_saved_txt.py` を作成。P3-369の**生成物に限った**設定SQLiteをSHA照合・読取コピーして、新しい `artifacts/p3-371-s07-next-txt-generated-r*` 内に設置する。前回GUIで保存した設定の修正・上書きはしない。新規 `create_services(FastAPI(),新規root)` の実製品サービスを用いて、作者91の生成小説501のpreview→実enqueue→所有ジョブ完了→保存receiptとファイル拡張子を観測。ネットワークへ出るsocket/DNSは検査側で遮断。

各試行は別fresh rootで行い、旧試験の成功や失敗を上書き・再送していない：

1. r1: `artifacts/p3-371-s07-next-txt-generated-r1/result.json` SHA256 `74afb4801158ec6ea9d6291b303aedc852f69c85420ed2e853af8b69f845c6c7`。結果 **FAIL** `ManagedUgoiraError: mp4_environment_unverified`、step1のみ。小説のTXTだけを確認する目的で作者全媒体をpreviewしたため、今回MP4のコーデックが未準備という非依存の環境条件が先に作用した。環境の `import cv2` は `ModuleNotFoundError`。MP4実保存のPASSは付けない。
2. r2: `artifacts/p3-371-s07-next-txt-generated-r2/result.json` SHA256 `80f0d25f26efdc10fa453d2eb1f08ef90d6f3eb7ee87f284f9b504a686507228`。対象を `author_media_preview('91',target_type='novel')` に限定。生成小説501のTXT候補と実ジョブ `done`、`.txt` 実ファイル有り・EPUB無しまで確認済み。ただし検査レシートの `Path.relative_to(out)` がWindows `\\?\` prefix付きroot表現との差で `ValueError` となりr2全体は **FAIL**。製品保存自体の失敗ではない。旧r2証跡は保持。
3. r3: 実製品は変更せず、検査レシート記録をファイルのbasenameへ変更。`artifacts/p3-371-s07-next-txt-generated-r3/result.json` SHA256 `7be2472b6f027b40bdad60d068ac4448ebd18d23f78cb02cb4efd72debbf3991`。**PASS**、新設定rootで `revision=1 / txt / mp4`、実作者媒体プレビューは小説 `TXT` を選択、実所有ジョブ `done`、保存された `receipt.json` の `output_format=TXT`、新実ファイル `Generated novel(501).txt`、EPUBは作成されない。コピー元の生成設定DB SHA不変。

実行コマンド：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-371-s07-next-txt-generated-r1
# success:false, steps:1, failure:ManagedUgoiraError, exit1
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-371-s07-next-txt-generated-r2
# success:false, steps:3, failure:ValueError, exit1
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-371-s07-next-txt-generated-r3
# success:true, steps:3, failure:null, S07_NEXT_SAVED_TXT_R3_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_p3_residuals_fixtures.py tests/test_novel_save_presentation.py
# 16 passed in 4.38s, S07_FORMAT_RELATED_PYTEST_EXIT=0
```

最終checker SHA256 `24e707ee4a9ee370cdfd531fe9862da488d41cbec9ecaf5be20eb99dcae3aa04`。r3プロセスは終了コード0で退出し、関連専用Python/Flet残留なし。実元ユーザーデータ変更なし、通信禁止、新生成rootだけを作成した。

## 担当内受入の限界

- **S07の今回追加条件：GUI選択済みのTXT設定→後続の合成実保存TXTを検証済み。** ただし実Pixiv小説、元Flet全製品実GUI同条件、EPUB/BOTHすべてのGUI選択とそれぞれの次回保存は独立した未検証条件。
- **S08の保存形式設定はP3-369 GUI選択・CAS保存・再読込まで検証済み。** MP4の実保存は `cv2` 欠如と `mp4_environment_unverified` により未検証。仕様の既定値や承認境界を変更して解消したことにしない。後続の正規コーデック環境と原画像/保存先owner安全前提が必要。
- S07/S08は正式 `0 PASS` 加算、レビューと本人受入は未実行。正式152／479・Stage5リリースゲート不変。独立P1/P2の解消は未判定。既成功K02/K04/K08/L13、K14 P3-368の試験は再実行せず、他担当差分・元DB/Cookie/worker/画像/ZIPは保護した。
