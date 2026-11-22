# D6 最終測定の到達点

**判定不能。** 試験用の隔離ゲートは開状態だった。通常製品の直前の非停止確認時刻は未受領で、実 GET 0件、残り97件。有効な実測ペア0。 [結果報告](../../../docs/P6_REAL_USE_RESULT_20261122.md) [GET 台帳](dispatch-summary.json) [実測集計](ranking-summary.json)

## 証拠

- 製品候補 `af6039b9a6c4e514d233f0ef3b2006aa98343c49`、Flet `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。製品コードの変更0件。
- 偽応答は交互2ペア、各版50件の一覧と先頭画像を確認。共通台帳は模擬14件、応答終了から次の送信開始まで最短876.874 ms。 [生データ](fake/ranking-samples.json) [画面・カード処理](fake/ranking-metadata.json) [模擬通信](fake/dispatch-summary.json)
- ネットワークガード付きの新規回帰6件と既存回帰71件が合格。実 HTTPS の応答時間と製品の原受入は未計測。 [新規回帰](pytest-final.log) [既存回帰](pytest-related.log)
- 最終偽試行中に、両アプリ、Playwright、Chromium 本体の PID と起動時刻を記録した。終了後はそれらの PID と起動時刻、測定用一時ディレクトリ、専用ポートで照合し、残存0、待受0。最終偽試行後の CPU 使用率は14%で、別担当の pytest と Codex の実行が残っていた。実測直前・直後の CPU 値は未取得。 [起動確認](fake/processes-running.json) [終了確認](host-fake-06.json)

## 監督へ引き渡す作業

本人または実機担当から通常ヘッダーの状態と JST の確認時刻を受け取り、非停止なら負荷確認と実測へ進む。当該ホストの外部処理が静まるまで最大60分待つ手順は、非停止確認後の実行時に適用する。中止時は証拠を保全する。 [再開手順](../README.md)

`S:/Work/_tmp/PV/d6-final-20261122` は今回作成した一時領域で、採用した証拠はこのディレクトリへ複製済み。監督の削除対象は `fake-01`〜`fake-06`、`pytest`、一時サブディレクトリ。ここに実測用 Cookie は配置していない。
