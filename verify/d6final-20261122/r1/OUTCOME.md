# D6 最終測定の到達点

**ランキングは測定条件下で達成。D6 原受入は判定不能。** 交互20ペアのクリック→先頭画像は Flet 642.6 / 923.8 ms、NiceGUI 421.0 / 851.7 ms（中央値 / p95）。低負荷4ペア、待機上限後16ペア。残る原受入は学習・ライブラリ・画像送り。 [結果報告](../../../docs/P6_REAL_USE_RESULT_20261122.md) [実測集計](ranking-summary.json)

## 証拠

- 製品候補 `af6039b9a6c4e514d233f0ef3b2006aa98343c49`、Flet `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。製品コードの変更0件。
- 第2回の実 GET は51件、全件200、残46件。最短875 ms以上の条件は876.824 msで達成。認証異常、429、403、CAPTCHA、予期しない5xxによる中止なし。ウォームアップ外の待機中に Flet のランキング GET が1件あり、台帳に含めた。 [GET 台帳](dispatch-summary.json) [実測生データ](ranking-samples.json)
- 同じホストで起動前と各ペア直前の負荷を観測し、高負荷では待機した。待機上限後の16ペアは EMA がCPUの45.30〜48.54%。測定後に両アプリ、Playwright、Chromium の残存0、専用ポートの待受0を読み戻した。 [負荷記録](host-observations.json) [起動確認](processes-running.json) [終了確認](live-host-after.json)
- 回帰6件を第2回の実測後に再実行して6件合格。第1回は既存回帰71件が合格し、偽応答で交互2ペアを観測。Sol の独立読取りは第2回の40試行を再集計し、全体の中央値 / p95、低負荷が4ペアであること、終了証拠を照合した。 [第2回回帰](pytest-live-followup.log) [第1回の既存回帰](pytest-related.log) [偽応答](fake/ranking-summary.json)

## 監督へ引き渡す作業

同じ隔離実データをそろえて未測定3操作を比較し、低負荷時は送信可能時刻を操作前にそろえてランキングを再確認する。画像送り用の複数ページ作品は今回の複製に確認できなかった。追加の実 GET を行う際は、残46件と取得停止確認を引き継ぐ。 [保存作品の確認](local-paging-inventory.json)

`S:/Work/_tmp/PV/d6-final-20261122` の監督削除対象は `live-02`、`gate-precheck-round2`、`fake-01`〜`fake-06`、`pytest` と一時サブディレクトリ。実測で使用した Cookie 複製は `live-02` にある。採用した数値と終了記録はこの証拠ディレクトリへ回収済み。
