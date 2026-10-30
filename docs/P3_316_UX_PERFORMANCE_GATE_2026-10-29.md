# P3 Stage4 — Flet/NiceGUI総合UX・性能・Windows原条件の差分

**判定：未受入。** 試験件数が多くても、以下の条件差が残る限り「原版より快適」とは宣言しない。2026-10-22の同一画像比較の旧候補と、2026-10-29の未commit候補314起点の配布実機結果は別のソース集合である。

## 比較可能な旧参考値（現候補へ継承禁止）

`docs/MATCHED_VIEWER_BENCHMARK_2026-10-22.md`の旧合成120作品/320PNG・1920×1080物理client・DPI168（175%）・同じ主画像868×579物理px、warm、間隔600ms、Flet→NiceGUI→NiceGUI→Flet各30遷移・生標本60/60と90ms前後の画面取得負荷を含む観測：

| 指標 | 隔離Flet | 旧NiceGUI | 今回の候補 |
| --- | ---: | ---: | --- |
| p50 | 231.760ms | 115.096ms | **未測定** |
| p95 | 250.330ms | 122.386ms | **未測定** |
| private bytes（root＋観測子合計） | 408.199MiB | 539.828MiB | **未測定** |
| 画像面積一致 | 旧比較の3ページのみ一致 | 旧比較の3ページのみ一致 | **未照合** |
| cold起動・代表操作・長時間時UX | 未比較 | 未比較 | **未比較** |

上記は2026-10-22の旧候補・隔離viewerで、全製品shell・実Pixivデータ・今回の314起点変更の性能改善を証明しない。旧NiceGUIはその比較でメモリ使用量が大きかった。原験計測器の詳細は`docs/NATIVE_BENCHMARK_METHOD.md`。単純にcapture負荷中央値を引いて「純画面応答」を作らない。

## 今回の実機で確認済み（同条件性能比較とは別）

- `artifacts/p3-315-native-build/frozen-v03-a/source-manifest.json`に固定した142製品ソースの集合SHA `33269aecf8dd1668c77ca6012f809a738da148bf32d24a4418c6375101e3866f`。既存HEAD `2cf4a7a`に対してdirty変更を含む**非コミット暫定凍結**。
- 同sourceからWindows PyInstaller onedir/noconsole 1050ファイルを生成し、独自JSとexeのhashを確認。別フォルダへコピーした1050ファイル全SHA一致（`standalone-r1/byte-match.json`）。
- 同開発Windowsマシンでnative WebView2起動、50カード、実decode、ビューアとタブ、学習Undo、ライブラリ作者詳細、Win32最小化→GDI実画素復帰、所有WM_CLOSE＋WinForms確認OK、子PID消滅、ポート解放、強制0を確認。`native-check-r3/native-acceptance.json`および`standalone-r1/native-{first,restart}.json`。
- ただし今回観測のWebView `devicePixelRatio=1`・client CSS約1872×961と、旧2026-10-22のDPI175%・1920×1080は**異なる**。このままp50/p95/最大・描画面積・レスポンス優劣を比較するのは誤り。
- 別フォルダ配布の合成ジョブ初回・再起動では保存2作品4ページの実ファイル・receipt・SHAが一致、ジョブ履歴削除でも保存ファイル不変、通常終了とport解放が成立。証跡`standalone-r1/jobs-{first,restart}.json`。実SQLite・実Pixivデータの保存互換は別受入。
- 終了確認を無視した旧checkerの`WM_CLOSE`単独ではOS確認が残り、プロセス/port解放がFAIL（`native-check-r2`）。確認機能を外すのではなく、製品所有PID/作成時刻のnative`OKCancel`のOKだけ選択するchecker改修後`native-check-r3`でPASS。失敗記録を上書きしない。

## Stage4残る必須ゲート

1. 現候補の実Win32 GUIを同条件の旧Fletに並べ、**実PNG描画矩形・余白・操作順序・フォーカス/タブ/戻る保持**を原期待操作で比較。
2. 旧と現で同一合成fixtureと1920×1080物理client・DPI/実画像領域一致・warm/cold区分・同じ入力600ms間隔・同じcapture/校正/負荷計測を実施し、全標本・失敗を含めてp50/p95/maxを算出。元比較は旧候補の参考値にとどめる。
3. 1280×720/1920×1080のDOM GUI以外に、native通常窓/最大化/DPI/トレイ/URI/確認付き終了・再起動/長時間、Win11の別PCでonedirコピー使用を実受入。別PC・全DPIは現在**未検証**。
4. 実Cookie GET承認範囲での代表的ユースケース、旧SQLite/ZIP/画像の保全、混在媒体・保存/復元・容量/例外・ユーザー操作が同等であることの証拠。
5. 原152各IDの操作本体・期待値・観測と、横断した独立レビュー/CI/製品ソースSHA・manifest一意性の合格。

**Stage6 Goalは未達**。Windows一部成功や旧参考値を「現候補のUX/性能合格」に読み替えない。
