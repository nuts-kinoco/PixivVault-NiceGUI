# 取得の中止通知　第12回検証

基点 `6182ec76`（製品は `f74c6656` と同一）、固定候補 `4bcbfc83`。Argon 指摘は第11回にも残っていた。第12回の隔離検証を記録する。原受入には同一候補の独立再検証と監督側の実機表示が残る。 [棚卸し](SWEEP.md) [候補・所有](candidate-state.txt)

| 承認済みの完了条件 | 確認したこと |
| --- | --- |
| 取得側が拒否する間は消えない | 稼働リーダーの固定通知は、観測のみが正常化しても保持する。明示再確認でも、永続 STOP、共有番兵、番兵作成権限、物理 R/W SQL、古くなった選択、429 ラッチ、未確認の HTTPS 後処理が残る場合は解除しない。別ゲートや shared 専用リーダーの成功を dual 経路の証拠にしない。 |
| 消えるのは実取得側の成功または利用者の明示確認 | 通知欄に「取得停止を再確認」を追加。所有・保存選択・旧履歴・物理ゲートの実許可経路を無送信で確かめ、元の取得経路に関係する通知だけを即時解除する。選択世代、観測上の証明、観測回数には依存しない。通常取得の成功も自動解除の証拠になる。 |
| 新規異常は1周期以内 | 監視中に新しく見つかった共有番兵を1周期で表示した。再確認が通った直後に番兵や既存の取得チェックが拒否へ戻り、監視が既存通知へまとめる競合でも解除を保留する。物理 R/W、共有ロック、DB ハンドル、所有元、SQLite 排他、番兵作成権限を検証した。 |

## Argon の再現と修正

- **M1** 実際の応答から合成 Cookie を採用すると、登録済み全リーダーを共通のバインド入口で更新する。旧リーダーの遅着拒否を現世代の選択証明にしない。観測に意図的な不一致を与え続けても、現リーダーの実許可に成功すれば手動解除できる。
- **M2** Draft は入力中の候補と保存済み選択を区別し、保存済み選択の世代を共用ポリシーへ登録する。Native の手動再確認は現在の保存済み取得経路を使い、ローカル STOP を共有へ転記しない。
- **L1** ゲート付き全種別のバインディングを共通化。交換時は解除回数と停止元縮小候補をリセットし、local と shared を持つ経路の選択世代を shared 専用経路から分ける。
- **Dots M02**　基点からの既存の制約。GET の冪等性を前提に、送信後・応答前の既存の再試行と共有番兵条件を維持した。 [第11回で確認した基点の証拠](../r11/baseline-m02.txt)

## 実行した試験と主ログ抜粋

- 基点で **11失敗**。追加の独立読み取り確認に基づき、共有 STOP の意図しない転記と通知の物理経路混同で **2失敗**、同じ通知にまとめられる番兵再発で **1失敗**を再現した。初回の回帰 **179通過**。追加で物理 R/W／親の権限 **2失敗**、ロック／DB ハンドル／所有元 **3失敗**、SQLite 排他 **1失敗**を再現した。追補関連 **514通過**、SQLite 排他まで加えた入口・P6 等 **36通過**。再確認順を戻した版の関連41ファイルは **1,305通過・1スキップ**。 [最終関連](final-related.log) [基点 red](red.log) [独立確認後 red](review-red.log) [再発 red](race-red.log) [関連514件](release-regression.log) [最終](release-entry-final.log)
- 中間関連試験は **1,294通過・3失敗・1スキップ**。SQLite close の従来試験2件には、観測正常化のみで通知を解除しない条件と実許可を追加した。残る1件は新規 NiceGUI 試験が共用の暗黙 UI コンテキストに依存した組合せだったため、その試験を専用の NiceGUI Client に隔離し、失敗した旧試験と一緒に再実行した。 [中間関連](related.log) [最終回帰](final-regression.log)
- 初回候補の全体試験は、実ゲート拒否が再確認の途中に戻る競合を独立確認で見つけ、6%台で所有プロセスだけを中断した。その実行の一時領域も回収済み。追補候補 `f37644d3` も、指紋再確認段階を飛ばす退行を収集順と照合して52%台で中断した。旧割り込み試験 **16失敗** を単独再現し、同じ **16件合格** を確認した。両実行の一時領域を回収した。 [初回中断](superseded-full.log) [追補中断](phase-regression-full.log) [収集順の照合](collection-diagnosis.log) [退行 red](phase-regression-red.log) [退行 green](phase-regression-green.log)
- P6 は指定の `from-stages` で8キーを再キーした。同じ送信経路の分類を維持し、未審査0と LF を確認。各試験は `required_network_guard` で実 Pixiv への通信0。 [P6](p6-rekey.log)

```text
red.log: 11 failed in 5.38s
review-red.log: 2 failed in 2.31s
race-red.log: 1 failed in 2.29s
final-regression.log: 179 passed in 53.07s
permission-race-red.log: 2 failed in 2.55s
authority-race-red.log: 3 failed, 2 passed in 3.39s
exclusive-race-red.log: 1 failed in 2.62s
release-regression.log: 514 passed in 115.09s
release-entry-final.log: 36 passed in 14.81s
phase-regression-red.log: 16 failed, 16 deselected in 4.65s
phase-regression-green.log: 16 passed, 16 deselected in 4.51s
final-related.log: 1305 passed, 1 skipped in 238.04s
p6-rekey.log: mapped 8 removed stale 8; issues after: 0; LF
```

## 全体 pytest

固定候補 `4bcbfc83` で1回完走。**5,390通過・1失敗・3スキップ・DPAPI 3除外**、26分08秒。NiceGUI 設定ダイアログは、全体実行でグローバル UI と `ui.page` の併用を拒否され、サーバーが起動しなかった。同じ候補の単独再試験は **1通過**。全体集計は1失敗のまま記録する。 [全体ログ](full.log) [実行記録](full-state.json) [単独再試験](settings-recheck.log)

```text
full.log: RuntimeError: ui.page cannot be used in NiceGUI scripts when UI is defined in the global scope.
full.log: 1 failed, 5390 passed, 3 skipped, 3 deselected, 15 warnings in 1568.05s (0:26:08)
settings-recheck.log: 1 passed in 8.45s
```

## 後始末と次の作業

実行記録23件と今回の隔離領域・所有プロセスを照合した。一時ディレクトリ、ファイル、容量、所有プロセス、未完了記録はすべて残存0。実 Pixiv への通信、実機操作、push は0。 [後始末](cleanup.txt)

次は同じ候補での独立再検証と監督側の R8 実機表示。全体試験で残った NiceGUI 1件の扱いを含め、製品受入と統合可否を判断する。
