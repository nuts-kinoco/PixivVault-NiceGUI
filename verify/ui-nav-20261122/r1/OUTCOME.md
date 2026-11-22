# ナビゲーション再編の設計検証

- 候補の分岐元は `pv-dev-baseline` の `29c0d86f723fe3ff492bb1ec4e2a294838866f02`、作業ブランチは `ui-nav`。
- 成果は [設計書](../../../docs/design/UI_NAV_RESTRUCTURE_20261122.md) と [ワイヤーフレーム](../../../docs/design/UI_NAV_RESTRUCTURE_20261122.html)。コードの参照と UI 修正 1〜5 の未統合差分を設計書に記載した。
- 今回の設計・ワイヤーフレームを検証した。製品側の実装、pytest、製品スモーク、正式受入、独立監査、Native と A17/B9 は実装候補で実施する。元の台帳・監査分母は変えていない。

## 実行方法

作業ツリー直下の PowerShell で実行する。対象は同ディレクトリの [check_wireframe.py](check_wireframe.py)。

~~~powershell
$env:TEMP = 'S:\Work\_tmp\PV'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -B verify/ui-nav-20261122/r1/check_wireframe.py
~~~

Playwright と既存 Chrome を使用。アプリ・サーバーを立てず、S: の隔離プロファイルでローカル HTML を開いた。ページの HTTP(S) リクエストはルートで拒否し件数を確認する。静穏フラグが存在する場合はブラウザを起動しない。

## 原出力

HTML 作成前の RED（終了コード 1）

~~~text
RED: navigation wireframe is missing
~~~

HTML 作成後の最終 GREEN（終了コード 0）

~~~text
PASS navigation, keyboard, focus, settings, launch targets, badges, shared log, and tag selection
dark:
PASS 1024x768: 2 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 1280x800: 1 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 1920x1080: 1 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 640x400: 2 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 320x540: 5 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
light:
PASS 1024x768: 2 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 1280x800: 1 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 1920x1080: 1 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 640x400: 2 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 320x540: 5 navigation row(s), labels and 7 icons fit, no page overflow, overlays fit
PASS 0 HTTP(S) requests and 0 page errors
PASS isolated browser profile removed
~~~

640×400 は 1280×800 の 200% 相当の CSS ビューポートで、ブラウザの実ズーム操作は未検証。配置・フォーカスは検査スクリプトの観測値。

## 逸脱と引継ぎ

| 発見 | 原証拠と実施した行動 | 残る検証 |
| --- | --- | --- |
| 現候補の詳細更新は 1 秒ではなく 5 秒。定期チェックの更新には照合と通知も含まれる | `scheduler_view.py:93,435–509`、`metadata_view.py:105–132`、`shell.py:131,208`。両パネルの詳細監視と要約バッジ、既存通知の経路を設計書に記載 | 開閉・切替・アカウント切替時の負荷と送達を製品テストで確認 |
| 現候補の CLI で `scheduler`／`metadata` は未定義。設定画面に起動先項目は見当たらない | `startup_tab.py`、`bootstrap.py:1166–1170`、`preferences_view.py`。明示指定の受理、設定を設ける場合の優先順、既定フォールバックを設計 | 実装候補の CLI、設定読戻し、起動方式別の利用可否 |
| 1280px の 1 行案と既存の 2 段裁定が併存 | FL-V03 とワイヤーフレームの 1280×800 実測。可変等幅の 1 行案と等幅 2 段案を記載 | 統合候補のラベル・操作域・のりしろを確認し、監督が裁定と照合 |

## 後始末

最終試験中に親 Python と既知の子孫 Python／Node／Chrome を 7 PID 観測し、終了後に同じ PID を確認した。試験用プロファイルは試験終了時に削除し、他担当のプロセスやファイルは対象外とした。

~~~text
Owned PIDs still running: 0
Owned browser profiles remaining: 0
~~~

割り当て時から存在した作業ツリーは成果の回収用。既存の `.workdir.json` は統合後を削除条件としている。次は監督が UI 修正 1〜5 の統合候補に設計を照合する。
