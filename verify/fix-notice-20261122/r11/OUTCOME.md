# 取得の中止通知　第11回検証

基点 `25ddea25`、実装候補 `f74c6656`。Sol と Dots が共通に指摘した終端 429 と物理 R/W SQL の通知保持に対応した。原受入は同一候補での独立再検証と実機表示、既存制約の扱いが残る。 [棚卸し](SWEEP.md) [候補と実行の所有](candidate-state.txt)

| 指摘 | 実装と実測 |
| --- | --- |
| H　429 の記録と送信の競合 | 上限到達と終了異常付き bounded 429 を取得終了処理で記録し、ポリシーの停止世代を進める。独立した2リーダーで上限1と上限3＋共有ロック close 異常を検証。先行の合成 sendall 1回、後続0回。旧履歴のロックを解放してから close が異常になる場合も、既存のプロセス内排他をその終了まで保持し、後続の送信0回を確認した。標準 `http.client.send` 監査イベントが戻った直後に停止世代を照合し、そこで確定していた場合の sendall は0回。永続 STOP の範囲と、既存の上限未満の待機・再試行を維持した。 |
| M-a　通知を解除する物理 R/W SQL の証拠 | 物理 R/W の接続設定・スキーマ・状態行の拒否を型付きのメモリ証拠に保存する。同じ DB とロックで実取得の SQL・接続終了・許可が成功し、その後に正常観測が2回揃うまで保持する。別リーダーが同じ DB とロックを開き直しても照合できる。mutable URI だけに異常、immutable URI は正常とする注入を local／shared で計8件検証した。 |
| M-b　最後の照合で初めて得た停止 | `verify` の読取直後に 401 STOP または未解消の共有 GET 番兵を置き、最後の指紋で見つけた同じ周期に表示した。既知の STOP と結果不明が両方あれば保持する。競合側で解除回数を進めない。 |
| L01　旧所有マーカーの終了異常 | 初回選択と取得時の再検証の両方で、close 単独と読取＋close の併発を区別し、一次拒否と shared の終了異常を併記した。対象4件の送信0回。 |
| Dots M02　新規 HTTPS の送信後・応答前の分類 | 基点 `pv-dev-baseline` の `9c5b9bf9` に同じ経路が存在する。今回の枝に由来しない。読み取り GET は冪等だが、再試行や共有番兵を保持する条件まで変わるため、基点の通信・回復契約は維持した。監督の判断に残す。 [基点の原証拠](baseline-m02.txt) |

## 実際の試験とログの要点

- 主回帰は基点で **13失敗**。二者の 429 競合2件、監査イベント内で停止する1件、物理 R/W の注入6件、最終指紋2件、旧マーカー2件。追加の読み取りレビューで再現した旧履歴 close の競合と回復証拠の引継ぎは **2失敗**。修正後の第11回は **19通過**、P6 を含む同ファイルの実行 **26通過**。最終回帰は **110通過**、既存の HTTPS、429、実 SQLite 排他、リーダーの再取得を含む。 [基点 red](red.log) [追加 red](review-red.log) [第11回回帰](release-r11.log) [最終回帰](final-regression.log)
- 合成試験の `http.client.send` と fake sendall で引き渡しを数えた。停止世代ロックは sendall に跨がせず、最終判定後の Python の復帰とメソッド呼び出しは実行中として扱う。実 Pixiv へのネットワーク通信0、実機プロファイル未使用。
- 指定の `from-stages` を実行して6キーを更新し、既存の owned GET の明示的な sendall を1件登録した。未審査0と LF を確認。 [再キー](p6-rekey.log)
- 初回重点には、close 注入試験のスレッド外の参照、旧テストの物理 SQL 再確認手順、P6 の未更新が残っていた。旧テストは観測だけでは解除しないことを追加し、実取得側の検証後に従来どおり2回で解除することを確認した。同一ゲートの再オープン試験ではリーダー生成時の実許可検査を把握し、観測だけの区間をその前に置き直した。合格閾値を変更していない。 [初回重点](first-green.log) [中間関連試験](release-focused.log) [最終回帰](final-regression.log)

第三者向けの主ログ抜粋

```text
red.log: test_terminal_429_is_latched_before_releasing_both_get_gates (2) FAILED
red.log: test_sendall_rechecks_429_after_the_stdlib_http_audit_event FAILED
red.log: 13 failed
review-red.log: 2 failed
release-r11.log: 26 passed in 17.40s
final-regression.log: 110 passed in 40.91s
P6: Final removed/added keys: (6, 7); issues after manual mapping: 0; LF.
```

独立の読み取り再確認では、旧履歴 close と同じ物理ゲートの証拠の修正後に、今回の範囲で追加の阻害は見つからなかった。製品の原受入は別途判定する。

## 全体 pytest

固定候補で1回完走。**5,362通過・2失敗・3スキップ・DPAPI 3除外**、32分03秒。NiceGUI の設定ダイアログは全体実行でグローバル UI と `ui.page` の併用を拒否され、単独は1通過。20,000件描画は全体で1.18244秒（閾値1.0秒）、単独は1通過。コード・期待値・閾値を固定した。全体実行の判定は記録どおりの2失敗。 [全体ログ](full.log) [実行記録](full-state.json) [設定の単独](settings-recheck.log) [描画の単独](bulk-recheck.log)

```text
full.log: RuntimeError: ui.page cannot be used in NiceGUI scripts when UI is defined in the global scope.
full.log: assert 1.1824403000064194 < 1.0
full.log: 2 failed, 5362 passed, 3 skipped, 3 deselected, 15 warnings in 1923.19s (0:32:03)
settings-recheck.log: 1 passed in 8.16s
bulk-recheck.log: 1 passed in 2.25s
```

## 後始末と次の作業

実行記録10件と今回の隔離領域・所有プロセスを照合。一時ディレクトリ、ファイル、容量、所有プロセス、未完了記録はすべて残存0。実通信と push は0。旧回の領域、先行作業の既存メタデータは範囲外。 [後始末](cleanup.txt)

次は同じ候補での High／Medium の独立再検証。Dots M02 の既存制約、全体で失敗した2件の受入上の扱い、R8 実機表示を監督側で判断する。
