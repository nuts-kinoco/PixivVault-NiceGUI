# 取得の中止通知　第8回検証

基点 `1915480a`、実装・試験候補 `30795592`。同じ候補で重点・全体・設定ダイアログ単独を実行した。取得停止に関する指摘は隔離検証で修正を確認。**原受入の確定は保留。** 全体に既知の設定ダイアログ起動失敗が1件あり、R8 実機表示と High／Medium の独立判定が残る。 [棚卸し](SWEEP.md) [候補と実行の対応](candidate-state.txt)

| 指摘 | 修正と結果 |
| --- | --- |
| Sol Medium 1　R/W SQLite close 異常で固定通知が消える | 取得・観測で既存 DB の R/W 接続と終了処理を共用。観測は `mode=rw` で SQL を発行せず、immutable 側だけで行を読む。local／shared の両方で故障の継続中は unverified・固定通知を保持し、復旧後に連続2回の確認で解消した。 |
| Sol Medium 2　標準 HTTPS の cleanup と429 | 所有プールの現役退役と idle 退役を共通の終了処理に通した。実装の標準経路で local／shared／Draft 各経路の上限1／3を確認。初回の合成応答が429で終了異常を伴うと待機0、送信は初回1・続行0、一次429と保存異常の両方を保持。早期停止は表示上も上限到達と区別する。 |
| Neon Medium 1／Sol Low 1　終了異常の誤帰属 | 各資源を取り扱うスコープで authority を捕捉する。外側ローカルの close／unlock と内側共有の結果不明、外側共有の旧履歴 close と内側ローカル拒否の双方で、異常が起きた側の固定通知を保持した。 |
| Sol Low 2　最終確認で見つけた STOP／結果不明の表示遅れ | 変化後の指紋と一次結果を照合し、その周期で STOP／結果不明を発行する。すでに読んだ一次結果は、後続の busy、接続・ファイルハンドルの close、プロファイル再読の失敗があっても保持する。 |
| Neon の追補 | 初回セットアップのチケット close で取消を保持。POST の401／403停止が確定して終了異常もある場合は応答結果を indeterminate とする。共有 intent 親の作成権限拒否と、Win32 21 を不在へ丸めないことを確認。 |

観測の R/W 接続に SQL authorizer で全 SQL を禁止した状態でも観測が通過した。観測前後の DB 内容・mtime が一致し、ゲートロック取得と intent 作成は0。HTTPS の終了異常や明示 unlock のようにファイル観測だけで検査できない資源は、取得で得た証拠を保持する。確認が完了した後で発生する外部変更は継続監視の次の周期で扱う。 [試験と処理の対応](SWEEP.md)

## 試験

- 基点の初回再現は **14失敗・1通過**。起動チケットと POST は別に **3失敗**。Draft、逆方向の unlock、idle の非 Timeout 異常を基点の別コピーでも確認し **5失敗**。 [初回 red](red.log) [起動・POST](additional-red.log) [基点の追補](supplement-baseline.log)
- 採用前の補助確認では、idle 拒否 **1失敗**、メタデータ Win32 21 **2失敗**、既読停止と profile close **1失敗**、共有マーカー／intent の二重異常と早期429表示 **3失敗**、既読 STOP／結果不明の最終指紋の組み合わせ **6失敗**を再現した。 [idle](idle-red.log) [メタデータ](metadata-red.log) [再読](seal-owner-red.log) [追記と表示](secondary-red.log) [最終指紋](known-outcome-red.log)
- 最終重点は **1,379通過・1スキップ**、4分20秒。早い段階の関連試験で7件を検出した。うち4件は、R/W URI または lstat の変更で既存の故障注入先がずれていた。3件は最終確認で即座に STOP を返す新要件を、以前の changed と照合していた。故障対象と根拠を維持し、注入先と STOP の期待値を更新した。Sol が最終差分を読み取りで確認した。 [初回関連結果](broad-green.log) [最終重点結果](release-green.log) [最終重点の実行記録](release-green-state.json)
- P6 は指定の `from-stages` で **14キー**を再キー。同じ通信呼び出しと分類、未審査0、LF を確認した。pytest は `required_network_guard` を適用し、実 Pixiv 送信0。 [P6照合](p6-rekey.log)

## 全体 pytest

最終候補で全体 pytest を **1回**実行。**5,273通過・1失敗・3スキップ・DPAPI 3除外**、27分22秒。Chromium の参照を設定し、TEMP／TMP、試験用プロファイル、共有 STOP 探索先を S: に隔離した。 [全体ログ](full.log) [全体の実行記録](full-state.json)

失敗1件は既知の設定ダイアログ。全体内では NiceGUI がグローバル UI と `ui.page` の併用を拒否し、サーバーが起動しなかった。同候補の単独試験は **1通過、7.30秒**。 [単独ログ](settings-recheck.log)

## 後始末と次の判断

今回の一時ディレクトリ、ファイル、容量、所有プロセスはいずれも残存0。監督がこの候補と原ログを使い、Sol／Neon の独立判定、全体内の NiceGUI 失敗の扱い、R8 実機表示を確認する。 [後始末](cleanup.txt)
