# D6 第3回の終了報告

**低負荷ランキングは達成。D6 原受入は判定不能。** 交互10ペアのクリック→先頭画像は Flet 370.6 / 707.5 ms、NiceGUI 224.1 / 427.5 ms（中央値 / p95）。NiceGUI の残差中央値154.9 msは目標180 ms以下。D6 原受入には同規模実データでの学習、ライブラリ、画像送りが残る。 [結果報告](../../../docs/P6_REAL_USE_RESULT_20261122.md) [独立再集計に使用した生データ](ranking-samples.json) [集計](ranking-summary.json)

- 製品候補 `af6039b9a6c4e514d233f0ef3b2006aa98343c49`、Flet `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。製品コード差分0件。134件の通算を引き継ぎ、新規上限40件、静穏フラグの消失時の停止、クリック前のペース待ち、借用なしの起動を計測補助に入れた。
- 複製の取得停止ゲートは準備時・起動直前とも `blocked=0`。実 GET 31件、全件200、残15件、最短送信間隔877.495 ms。Flet の待機中GET 1件も含めた。指定された異常による停止はなかった。 [準備時ゲート](prepare-profile-stop.json) [起動直前ゲート](runtime-profile-stop.json) [GET 台帳](dispatch-summary.json)
- 各ペア直前の10回で低負荷を確認。全体CPU 11〜14%、EMA CPU 0〜6.50%。高負荷時はクリックを待った。保存した起動 PID と起動時刻を照合し、所有プロセス残存0、待受0。監督の静穏フラグは終了確認時に存在した。 [負荷記録](host-observations.json) [起動PID](processes-running.json) [終了確認](host-after.json)
- 必須通信ガード付きの回帰8件が合格し、偽応答は2ペアで先頭画像まで到達。Sol は停止条件と20試行の再集計を独立確認した。 [回帰結果](pytest.log) [偽応答](fake/ranking-outcome.json)

## 監督への引継ぎ

対象5項目の追加測定は0件。同じ学習データ、検索対象のある共通DB、複数ページ作品を先に確保する。マイブックマーク通常更新の最低80 GETは残15件を超える。監督の `D6_QUIET.flag` はそのまま残した。 [追加項目の根拠](additional-operations.json)

監督の削除対象は `S:/Work/_tmp/PV/d6-final-20261122/live-03`、`fake-07` と今回の pytest 一時ディレクトリ。実測の資格情報の複製は `live-03` にあり、数値と終了証拠は `r2` へ回収した。
