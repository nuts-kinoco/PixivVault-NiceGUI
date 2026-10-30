# Phase11 M4 単一小説保存UI候補 — fixture自己検証 (2026-10-26)

状態: **単一小説 fixture の保存UIを実操作で自己検証済み。独立監査・原全機能受入は未完。**
最新の本人指示で CoS は休止し、Codex がローカル fixture 回帰と隔離loopback GUIを担当した。
以前の「Codex未実行・CoS待ち」は `artifacts/m4-novel-save-ui-20261026/` の凍結証拠として保全する。
実サービス利用可能、M3監査合格、Phase11全体完了、main統合済みとは扱わない。

## 候補・原契約・依存

- 専用path: `S:/Projects/PixivVault-NiceGUI-m4-novel-save-ui-20261026`
- branch: `phase11/m4-novel-save-ui-20261026`、HEAD: `2cf4a7a7286dd728e8df8125094498a3622babba`。
- 原契約 `PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md` M4 の単一小説入力→プレビュー→形式→キュー→保存/失敗→ライブラリ閲覧を対象。
- 固定M3由来7ファイルは元候補・当候補とも旧manifestとbyte一致。M3集合SHA `715b2bd037f98736409ba70990c34fe2d90f4ddf1a0a1945f1af8f222a990e32`。
- M4所有11ファイルと固定M3 7ファイルのSHA、dirty、差分、source snapshotは `artifacts/codex-m4-acceptance-20261026-r1/final/manifest.json`。
- PR #33 reader、PR #35 うごイラを取り込まない。元M3監査ZIP/Base64/拒否された資料抽出には触れていない。

## 使用できるfixture導線

既存「ダウンロード」タブで架空小説ID 123 / 124 を確認し、EPUB推奨・TXT・BOTHを選択してキューへ追加する。
プレビューのみでは保存しない。形式/ID変更は選択を失効させ、再確認を必要とする。
確認・保存・receipt照合・本文読込は UI loop 外で行い、M3 の operation_id、attempt、CAS と永続 JobService の制御を使う。
キューで一時停止/再開/停止、失敗リストで再試行を操作でき、検証済み完了だけを保存済みと表示する。
ライブラリは active receipt と出力hashを照合した結果のみ表示し、TXT/EPUBの保存済み本文をプレーンテキストで閲覧する。
長文ページ切替・スクロール・読書ダイアログ終了後のフォーカス復帰に対応。同じ保存rootで再起動して結果を照合する。
`--novel-fixture-root <専用絶対path>` の明示opt-inを使う。実Cookie/実Pixiv/既存利用者データと混在させない。

## この継続で修正した実不具合

1. NiceGUI `ui.select` は `{value: option_index, label: ...}` を送る。文字列と想定して形式変更を捨てていたため、TXT/BOTHを選んでもEPUBになっていた。固定選択肢の有限整数indexから論理形式へ変換し、未知値・bool・範囲外・古いepochを拒否する。
2. 本文の非同期読込後にダイアログを開くと、終了後のフォーカスが「読む」に戻らなかった。現在のタブ・一覧世代・有効な行に限って復帰する。古い行/非表示タブへフォーカスを戻さない。

追加回帰は `tests/test_novel_save_view.py` の実イベント形13case、`tools/check_novel_save_browser.py` と `tools/check_novel_save_native.py` の実DOM/実ファイル手順。
テスト専用serverだけにnonce付きのgate・snapshot・shutdownを置き、製品へテスト制御APIを追加していない。

## 最終自己検証の実結果

- 全pytest: **2034 PASS / 2 SKIP / 0 FAIL / 0 ERROR** (`full-final.xml`)。
  SKIPは `test_managed_downloads.py:251` と `test_novel_storage.py:120` のWindows symlink権限。変更しない。
- Edge headless 1280×720 / 1920×1080、それぞれ初回・再起動: **4/4成功** (`edge-focus-fix/result.json`)。
- source-native WebView2 初回・再起動: **2/2成功** (`native-focus-fix/result.json`)。
  native内側viewportは1267×684。所有PID/生成時刻/HWNDを照合し、画素の最小化→復帰一致、WM_CLOSEによる正常終了、子プロセス残留0、app/CDP port解放を確認。
- 実クリックでプレビュー非保存、全3形式、pause/resume、故意の初回失敗→同じjobのattempt2成功、stop後未保存、重複投入抑止、4件のverified一覧、TXT/EPUB/BOTH本文、長文ページ、mouse wheel、Escape/閉じる後フォーカス、再起動前後receipt/output SHA一致を確認。
- 最終GUI全caseでpage/console errors 0、監視対象ページの外部リクエスト0、正常終了/port解放。画面を目視確認した。これはOS全体のネットワーク監査ではない。
- 全pytest・Edge・nativeのruntime source before/after SHAは全て一致。検証runner SHAも結果に固定。

表示切替は20回のDOM確認までのwall時間。p95はnearest-rank、バックエンド処理やAPI速度とは別の自己計測。

| 環境 | p50 ms | p95 ms |
|---|---:|---:|
| Edge 1280×720 / 初回 | 67.2 | 105.0 |
| Edge 1280×720 / 再起動 | 74.1 | 101.1 |
| Edge 1920×1080 / 初回 | 69.4 | 97.8 |
| Edge 1920×1080 / 再起動 | 78.2 | 111.8 |
| source-native / 初回 | 53.9 | 86.2 |
| source-native / 再起動 | 88.5 | 121.1 |

保存worker実処理は 61.7–149.7 ms（fixture gate待機を除く）。実ネットワーク時間ではない。

## 失敗履歴と再実行理由

- `edge-r1`: 検証コードが検索反映前に制御を押し、古い世代として拒否。反映世代の待機を追加。
- `edge-r2`: TXT選択時にEPUBのままという製品不具合を再現し、修正。
- `edge-r3` / `native-r2`: 保存経路は成功。その後フォーカスを明示検査した `edge-final` / `native-final` で復帰欠落を再現し、修正。
- `native-r1`: CDP準備完了と初期ページ表示を同一視した検証側の待機不足。about:blankから専用loopbackへの初期遷移を待つよう修正。
- 全失敗記録は保持。各プロセスの終了を照合してから新規fixtureで実行し、成否不明な保存操作を再送していない。

## 再現手順と期待結果

既存S側venvを使う。TEMP/TMPを当候補の新規artifact領域に設定し、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=<当候補>/src`。
インストール不要。runnerの出力先は存在しない新規dirを指定する。同じ旧証跡へ上書きしない。

```powershell
$python = 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe'
# cwdは当候補。$runは新規の専用artifact root、tempは事前に作成する。
$env:TEMP = "$run/temp"
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& $python -X utf8 -m pytest -q --basetemp "$run/tmp" --junitxml "$run/full.xml" -p no:cacheprovider
& $python -X utf8 tools/check_novel_save_browser.py --channel msedge --output "$run/edge"
& $python -X utf8 tools/check_novel_save_native.py --output "$run/native"
```

期待: 上記pytest件数と2SKIP、両runner exit0 / success=true。runnerは空きloopback portを選び、既存8089/PID37168を操作しない。

## 未受入・残件

- 独立Claudeレビュー、元M3独立監査、M5の配布native build→全file SHA copy→配布物操作、Windows/Ubuntu/Chromium exact-head CIは未実行/未完。
- 現範囲は合成の単一章TXT/EPUB/BOTH。実小説本文GET、作者一覧/ブックマークからの取込、複数章・挿絵、PR #33 reader全体、うごイラ全体は未接続/未受入。iPhone連携は対象外。
- 実データ・実Cookie・外部API/GPU、依存導入、権限/認証/OS設定、課金、Git commit/push/公開/main統合は行っていない。最新依頼の範囲内では追加承認を要する変更を必要としなかった。
- この記録は実装担当の自己検証であり、製品全体の完成・独立監査合格の宣言ではない。

書込み終了は最新 `artifacts/codex-m4-acceptance-20261026-r1/final/write-end.json` で確認する。
