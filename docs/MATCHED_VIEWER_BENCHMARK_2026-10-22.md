# 同じ画像描画面積でのwarmページ送り比較

2026-10-22、隔離した旧Flet viewerとNiceGUI native viewerで、同じ合成PNG・物理client・DPI・実画像描画矩形を揃え、各60遷移を測定した。**取得・照合負荷込みの観測p95はFlet 250.330 ms、NiceGUI 122.386 msだった。** この条件の観測p95は51.1%短い。private bytesのプロセス群合計は408.199 MiBと539.828 MiBで、NiceGUI側が32.2%多かった。

この値はwarmの独立したページ送りを対象にする。アプリ全体の高速化、純描画時間、FPS、初回表示、cold、連打、配布exeの性能を示す値ではない。

## 対象の実装と比較条件

| 項目 | 条件 |
|---|---|
| NiceGUI側の起動SHA | `f35bd54908f6504230550c4373e19a3676eb0734`。製品コードは学習保存の実装`1011127f4e86c2c3a34583b1aae2836b04655c21`と同一 |
| Flet隔離launcher | `6f2b66e53b275f0c614211e05d5f864e55c2f412`。原UI7ファイルの内容・dispatcher・画像fitを変更せず、外側のPage paddingだけを指定 |
| 起動方式 | 両側ともPython 3.13.11のinterpreter-native。Flet 0.85.3 / NiceGUI 3.18.0・WebView2。配布exe比較ではない |
| データ | 架空120作品、同じ320 PNG。fixture SHA-256 `f1406aa85562b6b1b477d71d236bf175433dcb35d70796f1957018b4f56b3524` |
| 対象作品 | 架空作品0003、3ページ、各PNG 640×427 |
| 物理client / DPI | 1920×1080 px、DPI 168（175%） |
| 実画像描画矩形 | 全3ページ・両backendで`[527,353,1395,932]`。右端/下端を含まない868×579物理px |
| 共通ROI | 左710、上400、幅500、高さ400物理px。両側の画像内に完全包含 |
| Flet Page padding | LEFT/TOP/RIGHT/BOTTOM = `10.5714 / 106.5 / 9.4286 / 4.8571` logical px |
| 入力 | 所有するinput HWNDへのWM_MOUSEMOVE / WM_LBUTTONDOWN / WM_LBUTTONUP。左右ボタンの中心座標 |
| 入力間隔 | 600 ms以上。入力前の待機であり測定値に含めない |
| 取得 | 両側`screen-gdi`。所有プロセス・位置・寸法・9点の被覆検査を維持 |
| 判定 | 対象templateとの平均RGB差≤1、直前ページとの差≥12。両側共通、各遷移2.5秒期限 |

配信側はFletのdata URI cache / NiceGUIのHTTP cacheについて全320画像を温め、NiceGUI側ではfixtureと実HTTP応答の全PNG hash一致も確認した。対象3ページは両側とも実表示・decode後に校正した。全320画像の配信cacheと、対象3ページの描画済み状態は別の条件である。

primeは各backendの`frame-0/1/2.png`の全画面6枚を開き、作品名・1/3、2/3、3/3のcounter・主画像を確認してから測定を承認した。実画像端を含む領域で描画境界を検出し、両側・全3ページで一致した。ROIの平均RGB絶対差は各channelとも0.4階調未満だった。描画結果には微小な画素差があり、全画素同一とは主張しない。

Flet側は原製品shellと背景ジョブを省いたranking/viewerの隔離入口で、NiceGUI側はデモshellである。paddingで主画像の描画条件を合わせたことは、原shell全体を移植したことを意味しない。

## 観測値

native message送信開始から、対象ページと一致した画面を取得してROI照合を終えるまでを測る。送信前のverifyは含まず、送信後の所有者検査・GDI取得・RGB比較を含む。各roundは同一プロセス内で30回、`0→1→2→1→0`を巡回する。開始時は校正済みpage index 2との一致を検査する。

| 順序 | 対象 | 成功 | p50 ms | p95 ms | 画面取得負荷p50 ms |
|---|---|---:|---:|---:|---:|
| 1 | Flet round 1 | 30/30 | 233.418 | 250.017 | 90.334 |
| 2 | NiceGUI round 1 | 30/30 | 114.262 | 120.806 | 88.401 |
| 3 | NiceGUI round 2 | 30/30 | 115.513 | 123.080 | 98.071 |
| 4 | Flet round 2 | 30/30 | 229.055 | 250.225 | 90.989 |

| 生の60標本を結合した値 | Flet | NiceGUI | NiceGUI側の変化 |
|---|---:|---:|---:|
| p50 | 231.760 ms | 115.096 ms | −50.3% |
| p95 | 250.330 ms | 122.386 ms | −51.1% |
| 最小 / 最大 | 182.159 / 254.937 ms | 87.618 / 126.471 ms | — |
| 別採取した取得負荷20回のp50 | 90.655 ms | 95.950 ms | — |

結合p95は2回のp95の平均ではなく、各backendの生60標本から線形補間で再計算した。取得負荷の中央値を応答値から差し引いて純描画時間とすることもしない。

順序はFlet→NiceGUI→NiceGUI→Fletとしたが、各1プロセスの反復で、独立起動を60回行ったものではない。最初のFlet round後に復帰状態の切り分けを挟み、各round終了は03:56:05 / 04:05:29 / 04:06:01 / 04:06:48 UTCだった。主測定中にビルド・pytest・実API probeを並走させていない。全PCの背景負荷を固定した実験や統計的な信頼区間の推定ではなく、この開発機の観測として扱う。

## プロセス群のメモリ

4round終了後、所有するroot/子孫のPIDと作成時刻を照合し、各5回採取した合計の中央値を示す。

| 指標 | Flet | NiceGUI |
|---|---:|---:|
| 観測プロセス数 | 4 | 10 |
| private committed bytes合計 | 408.199 MiB | 539.828 MiB |
| process working set合計 | 257.305 MiB | 694.934 MiB |

private bytesは約131.629 MiB、32.2%増えた。この条件でメモリ削減は確認できない。working set合計は共有ページの重複計上を含み得る。GPU割当を含む全システムのRAM使用量ではなく、最小化やページング、shell・process構成の差も残る。

## 開始前に拒否した試行と復帰

成功120標本とは別に、標本収集前の拒否を2件保存した。Fletは対象ウィンドウの最小化、NiceGUIは一様frameを検出して停止した。いずれも標本は空、p50/p95はnullであり、成功した遷移の遅延値として集計しない。未完走の測定から失敗標本を除いて成功p95を作ったものではない。

NiceGUIでは外部`ShowWindow(SW_RESTORE)`後にtop windowの復帰flag/寸法は戻ったが、WebView子windowの座標が画面外に残り、`screen-gdi`の一様frame検査が拒否した。DOM内の画像は読み込み済みだった。同一所有windowに`SC_MINIMIZE→SC_RESTORE`を1cycle送ると子座標が復帰し、実GDI全画面で作品0003・3/3・元の主画像描画領域をprimeと独立担当が確認した。以後の測定は校正page2一致を含む全ガードを再通過した。深い原因の解明・製品修正の完了とは扱わない。

`ImageGrab(window=...)`は正常な実GDI描画へ戻った後も白かったため、取得方式の制限として分ける。DOM screenshotやOSの非最小化flagだけで実表示成功と判定しない。既存native smokeの復帰判定はflag中心であり、子の配置と復帰後の実画素の安定一致を次の強化対象とする。

両backendはWM_CLOSEで通常終了し、観測4 / 10プロセスの残存0、NiceGUIの試験用2ポート解放、process log error 0を確認した。元UI7ファイルと元database.pyのhashは一致した。

## 実装・検証・独立レビュー

この変更の製品側UI差分はない。`tools/legacy_baseline.py`へ任意の`--page-padding LEFT TOP RIGHT BOTTOM`を追加し、未指定時の挙動を保つ。有限・非負の4値と指定寸法内のpaddingを、artifact読取/Flet importより前に検査する。`--prepare-only`との併用は拒否し、指定値を起動metadataへ残す。

既存10ケースを維持し、未指定・ゼロ・非対称padding、不正値、prepare-onlyの追加12ケースを含む対象pytestは22件成功。原UI7ファイルの内容と描画処理は変更していない。実装担当とは別の担当が対象コード、試験XML、全4measurementの生標本・校正metadata・描画境界・復帰画面を照合し、限定比較にblocking指摘なしとレビューした。

証拠はignoredの`artifacts/matched-viewer-20261022-01/`に置き、生画像・プロセス情報・ログはGitへ含めない。

- `padding-pytest.xml`、`geometry-comparison.json`
- `native-gdi/`と`flet-gdi/`の校正全画面・calibration・`measurement-1/2.json`
- `flet-preflight-failed.json`、`native-preflight-failed.json`、復帰script/JSON/GDI画像
- `summary.json`、`memory.json`、`finish-check.json`

今後は初回表示/cold、連続入力、原shellと配布物まで揃えた比較、100/125/150%と別PC、復帰後の実画素検査が残る。本比較の成功を段階0/1/3全体の完了へ換算しない。
