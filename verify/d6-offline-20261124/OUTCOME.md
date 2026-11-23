# D6 オフライン比較の結果

**5項目を測定し、NiceGUI の中央値は3項目で Flet 以下。D6 原受入は未達。** 作者検索とおすすめは中央値で遅く、同規模の隔離実データ比較が残る。対象は NiceGUI `fedf4ad5` と Flet `3ac3aa0d`。製品ソースの変更は0件。 [D6 本文](../../docs/P6_RESPONSIVENESS_2026-11-19.md) [原受入](../../docs/PV_DEFINITION_OF_DONE_2026-11-17.md)

| 操作 | Flet 中央値 / p95 (ms) | NiceGUI 中央値 / p95 (ms) | NiceGUI − Flet 中央値 (ms) | 判定 |
|---|---:|---:|---:|---|
| 学習の判定から次画像 | 141.4 / 226.7 | 114.1 / 140.4 | −27.4 | 同等以下 |
| ライブラリの作者検索 | 311.3 / 340.4 | 340.9 / 1,647.0 | +29.6 | 未達 |
| ビューアの画像送り | 166.2 / 183.4 | 140.7 / 165.0 | −25.5 | 同等以下 |
| おすすめ初回表示の先頭画像 | 676.7 / 1,086.3 | 739.6 / 959.9 | +62.9 | 未達 |
| マイブックマーク初回表示の先頭画像 | 927.9 / 1,207.5 | 412.0 / 3,653.7 | −515.9 | 同等以下 |

各版10回。nearest-rank p95 は10回中の最大値。判定は中央値で `NiceGUI ≤ Flet`。ランキングの既存値は前回候補の記録を参照。 [今回の集計](summary.json) [前回のランキング](../../docs/P6_REAL_USE_RESULT_20261122.md)

## 測定条件と検証

- 既存の `tools/p6_responsiveness.py` と `tools/original_run/` を使い、Playwright の隔離 Chromium（1280×800、ソフトウェア描画）で実際のクリック・入力・キー操作を行った。操作呼び出し直前から対象のカードまたは画像を確認し、描画2フレームを待つまでを計時した。画像の試行には画面キャプチャと画素照合の時間を含む。おすすめとマイブックマークは試行ごとに新しいページで、おすすめ、ライブラリ、マイブックマークの順に開いた。サーバーと OS のキャッシュは計測中に温まる条件。 [Flet 生データ](comparison/original-samples.json) [NiceGUI 生データ](comparison/nice-samples.json) [Flet キャッシュ再測定](cache-first/original-samples.json) [NiceGUI キャッシュ再測定](cache-first/nice-samples.json)
- 隔離合成 DB は作品2,000、作者200、学習履歴12、SuperLike キャッシュ12、マイブックマークキャッシュ20行。保存画像2,024枚、学習・おすすめ用画像60枚。画像は480×360 PNG。ビューア対象は25ページ。両版の DB 複製と候補画像の SHA-256 一致、SQLite の整合性を確認し、保存画像は隔離ディレクトリを共有した。学習の供給は Flet の候補アダプターと NiceGUI のデモ供給に差し替えた。Flet のビューアとキャッシュ画像はローカル画像を data URI にし、NiceGUI は製品のローカルメディア経路を使う。NiceGUI のおすすめ向けには同一の画像バイトを返す画像専用の偽リーダーを接続した。 [入力のハッシュと件数](comparison/offline-fixture.json) [再測定の入力](cache-first/offline-fixture.json)
- 比較は同一ホストで Flet → NiceGUI の順。観測4回でホスト CPU 26〜48%、EMA CPU 1.13〜1.52%、EMA GPU カウンターは取得0件で使用率未確認。操作ごとの負荷分離は未実施。Flet の Web 配布物にフォントが欠け、ローカルフォント 404 が比較1,007行、キャッシュ再測定2,981行あった。この差を含むオフライン Web 比較で、実データやネイティブ実機への外挿には限界がある。 [負荷サンプル](host-load.json) [ログ集計](run-log-counts.json)
- Flet の外部宛て試行は比較でサーバー側28件・ブラウザー側12件、キャッシュ再測定で1件・11件を遮断。NiceGUI の外部宛て試行は両方0件、偽リーダーは各60回のローカル読取・拒否0件。実 Pixiv への GET は0件。 [Flet サーバー](comparison/original-server-network.json) [Flet ブラウザー](comparison/original-browser-network.json) [再測定 Flet サーバー](cache-first/original-server-network.json) [再測定 Flet ブラウザー](cache-first/original-browser-network.json) [NiceGUI サーバー](cache-first/nice-server-network.json)
- ツールの自動点検は未実装時の読込エラーを確認し、必須通信ガード付きの関連・ツール pytest は最終23件 PASS（4.61秒）。ブラウザー計測は比較110.44秒、キャッシュ再測定101.95秒で、採用100試行を含む保存済み140試行のタイムスタンプ区間、中央値、p95 を再集計した。全体 pytest と外部レビューは実施範囲外。 [red 抜粋](self-check-red.log) [pytest 抜粋](focused-tests.log) [比較の完走記録](comparison/browser-result.json) [キャッシュ再測定の完走記録](cache-first/browser-result.json)

再現時は隔離用の新規ディレクトリを作り、`TEMP`・`TMP`・`TMPDIR` をその親の S: 配下に設定する。既設 Chromium の `PLAYWRIGHT_BROWSERS_PATH` および区間計測用の `PV_P6_SEGMENTS=1` を設定し、専用 venv の `python.exe -B` で次を実行する。現在のツールは初回表示2項目を学習より前に実行する。比較1の学習・検索・画像送りを単独で再現するときは1行目を使う。

```text
tools/p6_responsiveness.py --offline-cache --out <new-S-scratch> --repetitions 10 --case learning --case library_search --case viewer_next
tools/p6_responsiveness.py --offline-cache --out <new-S-scratch> --repetitions 10 --case recommend --case bookmarks
```

## 遅かった区間と改善候補

- **作者検索**は入力後から一致カード確認まで Flet 283.2 ms、NiceGUI 297.3 ms。NiceGUI の入力イベントから一致カードが DOM に出るまで約255.6 ms、Python の検索ハンドラーは8.4 ms。200 ms の入力待機とその前後の伝送・DOM 反映を切り分ける。最初の入力操作が1,272.6 msかかったため、初回の容量計測とカード画像読込との競合も確認対象。検索結果をキーで差分更新し、初回作業を入力と並行させない構成を比較できる。 [操作の内訳](comparison/nice-segments.json) [DOM 計測](comparison/nice-dom-segments.json) [サーバー計測](comparison/nice-server-segments.json)
- **おすすめ**は操作呼び出しが Flet 90.1 ms、NiceGUI 122.7 ms。操作呼び出し終了後から画像確認までは577.7 ms、582.1 ms。差が見えたのは主に操作を送る区間で、ブラウザーのクリック待機も含む。実入力、キャッシュ読出し、Vue の反映、画像 decode に分け、先頭画像の優先読込や節単位の描画を測る。 [Flet 操作内訳](cache-first/original-segments.json) [NiceGUI 操作内訳](cache-first/nice-segments.json)

## 逸脱と後始末

- 試走で旧セレクターが現候補の学習見出し・作者カードと Flet の複数行カード名を照合できなかった。測定ツールの照合を更新し、両版を完走した。初回比較では Flet の学習判定がその後のおすすめの材料に加わった。おすすめとマイブックマークの採用値は、学習を実行せず、初期状態を一致させた `cache-first` の値。初回比較の両項目も生データに残した。
- 先行コピーの画像区画を集計した。直近4世代はいずれも `cli-images` に画像0、`native-images` に3、`remainder-20261116` に4。これらから共通の作品、DB、複数ページ、学習画像の対応を確定できなかった。実機プロファイルを開かず、過去の同規模合成フィクスチャを再利用した。 [前回の隔離コピーで未取得だった項目](../d6final-20261122/r2/additional-operations.json)
- 比較・再測定の終了時に、起動したプロセスは残存0、使用した4ポートの待受も0。終了後の確認では22631に新しい待受があり、再測定終了後に起動した別プロセスのものと確認した。証拠を回収した一時領域 `S:\Work\_tmp\PV\d6-offline-20261124` は、実行環境が `Remove-Item` をポリシーで拒否したため残存。検証済み対象は8,073ファイル、842ディレクトリ（ルート込み）、231,580,282バイト。監督へこの範囲の削除を引き継ぐ。 [比較終了確認](comparison/cleanup-processes.json) [再測定終了確認](cache-first/cleanup-processes.json)

**次の作業**は、監督による一時領域の削除と、両版共通に使える匿名化済みの隔離実データの確定。応答改善の実装は今回の範囲外。
