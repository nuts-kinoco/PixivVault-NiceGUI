# 本人判断 3 件の実装と試験

候補は `small-dec` のコードコミット `08a6fb4d57eab6a255166337ded7227bb210ed26`。起点は `961caa2cd3b4d785eb3357eca320b01d4c1e7d61`。差分はローカルコミットに保存した。

## 原受入

| 判断 | 隔離試験の結果 | 残る受入 |
| --- | --- | --- |
| 失敗リスト | 絞り込み全ページの総件数・再試行件数・対象外件数、行ごとの理由、全件対象外の理由の要約を確認。従来のサービス判定が許可した行だけを再投入処理に渡すことを確認。 | Neon の独立検証と実機の画面確認 |
| クラッシュ記録 | 所有マーカー付きの起動プロファイルで `<profile-root>\logs\crash_log.txt` を bootstrap より前に作成。明示パスと環境変数の上書き、ログ格納先に書けない場合の警告・起動継続を確認。既存の記録 12 行を退避した。 | 初回に作るプロファイルでの既定捕捉と、配布ランチャーの既定を揃える変更は指定ファイル外。範囲確認後に対応。 |
| 借用プレミアム | 保存された認証の証明と復号情報から起動時に ON に復帰。ヘッダーの OFF は今回の起動に限り、次回起動で再び有効。未設定では非モーダルの案内と初期設定入口を表示。待機・枠不足は待って再試行する案内を表示。 | 現行の初回パスワード検証は固定の開発用検証情報に依存。D10 の本人専用登録と互換確認には認証範囲の承認が必要。旧版の永続 OFF で削除された保存済み復号情報も再設定が必要。Neon と保全・認証・並行動作の Sol 検証、実機確認が残る。 |

## Red と green

| 判断 | Red | Green |
| --- | --- | --- |
| 失敗リスト | [1 failed](red-detail-failed.log) | [1 passed](green-failed.log) |
| クラッシュ記録 | [3 failed](red-crash-owned.log) | [3 passed](green-crash-owned.log) |
| 借用プレミアム | [7 failed](red-verified-borrow.log) と [追加 1 failed](red-borrow-pool.log) | [8 passed](green-borrow-owned.log) |

クラッシュ記録の red は起点の `crash_evidence.py` をテストプロセスで読み込む形で再現した。旧コードの出力は隔離先へ向けた。

関連試験では、失敗リストの [無効時ツールチップ 3](regression-failed-tooltip.log)、[小説の再試行 12](regression-failed-novel.log)、[一括結果 2](regression-failed-aggregate.log)、クラッシュ記録の [例外と faulthandler 6](regression-crash-owned.log)、[情報の抑制 2](regression-crash-privacy.log)、[起動経路 4](regression-crash-entry-final.log)、[既存配布ランチャーの事前確認 3](regression-d10-preflight-final.log) が合格。借用の [認証 14](regression-borrow-gate.log)、[経路 24](regression-borrow-routes.log)、[ダイアログ回復 13](regression-borrow-recovery-final.log)、[待機と払い出し 12](regression-borrow-rotation.log)、[回復 UI 6](regression-borrow-standalone-recovery.log) が合格し、最終文言で [回帰 1 件を再実行](regression-borrow-messages-final.log) した。再実行分は件数に加えていない。

全体 pytest は `S:\Work\_tmp\PV\sup\D6_QUIET.flag` の解除待ち。解除後、監督と実行状態を照合し、重複がなければ指定 venv で `-B -m pytest -p required_network_guard -k "not dpapi"` を実行する。TEMP/TMP と 20 文字超の basetemp は S: の隔離領域、PYTHONPATH は候補の src と tests。PYTHONIOENCODING は未設定とする。

## D6 測定で借用の起動時復帰を止める

測定ハーネス起動前に親プロセスで次を指定する。現在の借用設定を保持したまま、その起動を OFF で開始する。

```powershell
$env:PIXIVVAULT_PREMIUM_STARTUP = 'off'
```

測定終了後、同じシェルで `$env:PIXIVVAULT_PREMIUM_STARTUP = $null` として通常の起動に戻せる。

## 実機で本人が確認する操作

1. 監督が候補を実機へ反映したら、保存済み認証が有効な環境で起動し、ヘッダーの「API切り替え」が ON になることを確認する。一時 OFF にして再起動すると ON に戻る。
2. 初期設定の案内が出る環境では、D10 で本人専用の資格情報を登録できる状態にしてから「初期設定を開く」を選ぶ。本人のパスワードと署名キーを入力し、UID 一覧の txt を選択して「有効にする」を押す。再起動後の ON を確認する。
3. 既存の起動プロファイルで、ログ上書きを指定しないアプリ起動のあと、プロファイル内の `logs\crash_log.txt` を確認する。D10 の現行配布ランチャーでは `-LogRoot` がクラッシュ記録を含む起動ログの上書き入口。既存プロファイルなら `-LogRoot <profile-root>\logs` を指定できる。

## 保全と逸脱

- 旧記録は `S:\Backup\PV\20261122-crash-evidence\crash_log.txt` と `SHA256.txt` に保存した。通常のハッシュ読取りは共有中の元ファイルで失敗した。共有読取りで元を二度読み、どちらの SHA-256 も退避コピーと一致した。元ファイルの整理は監督の担当。
- 借用の既存の回復案内試験が、利用枠の案内から設定への参照が消えたため一度失敗した。[失敗ログ](regression-borrow-recovery.log)。利用枠の文言に待ち時間の確認場所を戻し、合格を確認した。
- pytest が起動した対象プロセスは残存 0。旧コードの red 試験が作った作業ツリー直下のログを専用一時領域へ移動した。試験一時物の削除ドライランまで実施し、実行は監督の担当。[実行後の記録](postflight.json)
