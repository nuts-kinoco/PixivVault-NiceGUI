# 第 13 回 取得停止の固定通知

対象は `fix-notice` の実装候補 `1cdedbfe`（初回差分 `877c45b0`、追加修正 `b7f15c58`）。本人承認済みの明示確認版へ切り替えた。実機判定と同一候補の独立検証は残る。 [棚卸し](SWEEP.md) [候補とジョブ所有](candidate-state.txt)

| 承認済みの条件 | 実装と隔離試験 |
| --- | --- |
| 取得側が拒否している間は消えない | 識別した経路で、プロファイル・所有権・旧履歴・ロック・物理 R/W SQL の検査と共有番兵の作成、書込み、fsync、除去を行う。実際の GET と同じゲートのコンテキストだけを通る。途中で拒否した場合と 429 ラッチ中は保持する |
| 利用者の明示確認だけで消える | 成功 GET、背景観測、期限、停止元縮小による固定通知の解除をなくした。実経路が成功し、開始以降の拒否と交換がない場合だけ解除する。稼働中の兄弟リーダーを利用し、正式なゲート交換では対象を引き継ぐ。既読表示と通常の中止・429 表示は既存仕様 |
| 新規異常を 1 周期以内に表示 | 実際に所有権を確認したリーダーを登録すると監視を開始する。STOP、結果不明、保存異常、競合を見つけた周期に固定する。検出元の物理ゲートを関連付ける。明示確認が作った一時ロックは正常な明示操作の失敗に数えず、判明済みの拒否は反映する |

## 個別の検証

- Dots H と Sol M2 は、保存済み参照および共有ロックの拒否後、通知公開前で取得スレッドを止めた。明示確認が先に許可を得ていても通知を保持した。
- Sol M1 は共有番兵の作成（ENOSPC）・fsync・除去を、実 GET と同じ箇所で失敗させた。拒否が残る間は保持し、正常化後は送信せず解除した。
- Sol M3 は取り替えたゲートの認証成功を要求した。独立稼働中の別ゲートには解除証拠を転用しない。
- Dots M と Argon M2 は自動解除の廃止に対応した。通常 GET の成功と、長い GET 中および完了後の観測で既存の固定通知を保持した。
- Dots L と Argon M1・L1・L2 は、再確認不能時の表示、閉じた兄弟と稼働リーダー、保存設定なし／旧選択ファイルなしの Draft、未設定の公開 CDN を通した。
- 実装中の独立静的確認で、永続 401 が確定する途中の競合、Native の権威ある再確認とリーダー生成前拒否、明示確認自身の busy、同一区分で物理所有元を複数観測する経路を追加で確認した。別のゲートの busy を保持し、後続の明示確認で見つけた同一共有ゲートの STOP が先行した許可判定を無効にすることも再現した。最後に、ローカルが拒否し共有ロックを一度も取らなかった場合にも、別ハンドルによる共有側 busy を表示し削除を止めた。合成入力で、拒否を見逃さず正常な経路の解除を妨げないことを確かめた。

## 既存の制約

Dots M02 は基点からの送信後・応答前の GET に関する挙動。GET の再試行と共有番兵の扱いは本人裁定により現行仕様。 [第 11 回の基点確認](../r11/baseline-m02.txt)

## 実行結果

| 実行 | 結果 |
| --- | --- |
| 第 12 回候補で新しい回帰のみ | `14 failed, 1 passed`。交換の再現を解除判定まで揃えた単独 red は `1 failed` |
| 実装後の境界の追加 red | 401・Native の必須確認 `2 failed`、複数経路の観測・先行判定 `2 failed`、共有ロック未取得 `1 failed` |
| 通知・P6 の関連一式 | `910 passed in 234.48s` |
| 取得ゲート、Native、認証・Cookie、公開経路など | `511 passed, 1 skipped in 221.88s`。symlink 権限による既存 skip |
| 複数経路の修正後 | `67 passed in 31.80s` |
| 最後のロック取得境界の修正後 | `84 passed, 1 skipped in 30.82s`。symlink 権限による既存 skip |
| P6 通信棚卸し | 既存分類を維持。新しい admission-only 呼び出しを無通信に分類。指紋の更新後は `issues after: 0`、LF |

[初回 red](red.txt) [ゲート交換 red](red-exchange.txt) [401・Native red](red-boundaries.txt) [複数経路 red](red-route-overlap.txt) [取得されなかったロック red](red-acquired-lock.txt) [通知・P6](final-focused.txt) [関連領域](related.txt) [複数経路 green](green-routes.txt) [最終修正 green](green-acquired-lock.txt) [P6 再キー](p6-rekey.txt)

### 最終候補で完走した全体試験

`python -B -m pytest -p required_network_guard -p no:cacheprovider --basetemp <S: 隔離先> -k "not dpapi" --tb=short -q`。 `2 failed, 5416 passed, 4 skipped, 3 deselected, 15 warnings in 2112.70s (0:35:12)`。外部への実通信 0。3 件の symlink skip と、Edge 起動不可によるシェル試験の skip を確認した。 [全体ログ](full.txt)

- おすすめ画面の実ブラウザ試験は別プロセスが 90 秒でタイムアウトした。
- 設定ダイアログは全体試験で `RuntimeError: ui.page cannot be used in NiceGUI scripts when UI is defined in the global scope`。独立起動では同じ NiceGUI エラーは再発せず、Edge が起動後に DevTools 接続を確立できず 180 秒でタイムアウトした。 `DevTools remote debugging requires a non-default data directory`。 [設定ダイアログ単独](visual-isolated.txt)

- おすすめ画面の Edge 単独再試験も `TimeoutExpired … after 90 seconds`。 [おすすめ画面単独](recommendation-isolated.txt)
- ローカルに既存の Playwright Chromium を明示して両試験をそれぞれ単独実行した。設定ダイアログは `1 passed in 8.27s`、おすすめ画面は `1 passed in 13.42s`。試験本体・期待値・製品ソースは元のまま。隔離領域の起動設定だけで `BrowserType.launch_persistent_context` の `msedge` チャンネル指定を、保管済み Chromium の `executable_path` に置き換えた。Edge を用いた全体結果は上記のまま。 [設定 Chromium](visual-chromium-reference.txt) [おすすめ Chromium](recommendation-chromium-reference.txt)

旧候補 `b7f15c58` の実行は、独立確認で新たに見つかった未取得ロック経路を直すため 途中で中断。最終候補の全体試験は上記の 1 回を完走した。 [旧候補の中断記録](superseded-full.txt)




### 逸脱と後始末

- 初期の探索試験で長い Windows パスの接頭辞を pytest に渡すと、旧履歴用の SQLite immutable URI で読み取りが失敗した。隔離領域の削除には長いパスを使い、pytest には通常の絶対パスを渡すことで再試験に成功した。 [初期ログ](legacy.txt) [環境切り分け](environment.txt)
- 計測した 6 回の隔離領域で 63,984 ファイル、392,812,265 バイトを回収した。父子関係を確認して止めた旧候補の試験プロセスは 3。終了時の記録済み PID、試験ルートを参照するプロセス、および第 13 回一時ディレクトリは各 0。 [後始末](cleanup.txt)

### 次の承認済み作業

監督側が `1cdedbfe` と同一の製品差分で取得停止ゲートの二系統の独立検証（Medium 0）と R8 実機表示を照合し、統合を判断する。標準 Edge での両ブラウザ試験の環境再確認も残る。今回の独立静的助言は実装中の並行順序の指摘と修正に使用した。
