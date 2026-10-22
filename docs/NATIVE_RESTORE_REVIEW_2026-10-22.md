# Windows復帰後の実表示検査

## 対象と変更理由

コードSHA: `76ddc0fc2cadf36b635a4c3e02f649fe197c10a6`。基準はPR #7を取り込んだmain `1261d020be52b76007e3f0da8a6c4f769fad98b5`。

従来のnative checkerは、UI操作を検査した後に最小化し、`IsIconic=false`だけで復帰を合格にしていた。比較計測ではOS上の復帰とWebViewの配置・実画素が一致しない例があったため、復帰後の実表示を必須の条件に追加した。

変更は検証器とその試験だけ。製品の`src`、native入口、build script、依存定義・Windows lockは、配布受入済み`1011127f4e86c2c3a34583b1aae2836b04655c21`と`git diff --exit-code`で差分なしを確認した。今回の実機試験は、そのSHAの別フォルダーへコピー済みonedir/noconsole exeへ新しいcheckerを適用した追加受入であり、新規ビルドの結果ではない。

## 判定条件

- 検査前にtop HWNDのowner PIDと作成時刻を固定する。gate開始時とWM_CLOSE直前にも完全一致を要求し、別の既知owned PIDへ変わった場合も拒否する。
- 物理座標を読む前に、検査threadのper-monitor DPI v2を確認する。topと唯一の可視`Chrome_RenderWidgetHostHWND`について、PID/作成時刻、子所属、client寸法、原点、DPIを検査する。
- 一時的に自分のwindowをtopmostにし、既存の`OwnedWindow`の前後所有権・被覆検査を通したscreen GDIだけを取得する。baselineは非一様な全client画像を連続2回確認する。子寸法をtopと一律同じにせず、実際の健全なbaselineを保存する。
- 入力は従来の`ShowWindow(6)`→最小化確認→`ShowWindow(9)`を維持する。復帰後はbaselineと同じgeometry/DPI、baselineに対する平均RGB絶対差が1以下、互いにも同じ閾値内の連続2画像を要求する。標本間の待機は150ms。
- 復帰途中の`target_reparented`だけは、同じPID/作成時刻/HWNDを毎回確認しながら復帰期限内で再検査する。detachを検出した標本は採用・保存せず、前の画像と連続数を破棄する。所有者の変化、恒久的なdetach、画面外配置、異なるDPI、別scene、期限後の結果を成功にしない。
- 比較計算とPNG保存の後にも期限を確認する。元のtopmost属性をfinallyで復元し、失敗時も通常WM_CLOSEと終了監視を行う。全体予算から最大5秒（短い指定では全体の1/3）を通常終了用に予約する。
- `--inspect-ui`の有無にかかわらず実画素gateを実行する。可視で被覆されていない専用windowと利用可能なdesktopが必要であり、取得不能をskip成功にはしない。出力prefixは毎回新しくし、既存before/after PNGは上書きしない。

全画面の平均差は許容値付きの検査で、全画素の完全一致を意味しない。同期Win32 APIの完了後に期限を検査する設計であり、API呼出し自体を強制中断するものではない。

## 実機で見つかった一時遷移

最初の標準checker実行はUI操作とbaseline2画像が成功した後、復帰直後の`target_reparented`を検出して不合格になった。topmostの復元、通常WM_CLOSE、観測8プロセスの終了、ポート解放は成功し、失敗した試行も保存した。

独立の診断scriptで、同じtargetのPID/作成時刻/HWNDを保って追跡した。復帰後約0.109msと0.486msの観測では子所属がfalse、約51.187msの観測ではtrueとなり、元の原点・寸法・DPIへ戻った。診断は待機と読取だけで、追加の最小化/復帰、resize、子windowの移動・再所属操作はない。その後の実画像もbaselineとの差0.015766で確認できた。

この診断から、初回の即時拒否は今回観測した短い復帰途中の状態を待てていなかったと判断し、上記の限定再検査を追加した。約51msは1試行の観測時刻であり、復帰性能の分布や保証値ではない。先行比較で子座標が画面外に残った例も同じ原因だったと確定する証拠ではなく、製品側の復帰処理を修正したという意味でもない。

## 修正後の標準checkerによる受入

2026-10-22 14:04 JST、同じ配布exeでUI検査あり/なしの2回を実行した。WebView2 155.0.4283.62、175% DPI（168）、top clientは3336×1826物理px、子clientは3338×1827物理px。各回の前後でその寸法・原点・DPIが一致した。

| 検査 | UI操作あり | UI操作なし |
|---|---|---|
| 保存先 | `accepted-ui` | `accepted-plain` |
| 復帰前後の画面 | 学習・架空作品0002・1/120判定済み | ランキング・50/120表示 |
| baseline/restoreの安定画像 | 各2 | 各2 |
| 一時reparentの観測 | 1 | 1 |
| 復帰画像の平均RGB差（閾値1） | 0.018385 | 0.349535 |
| original topmost復元 | 成功 | 成功 |
| WM_CLOSE/観測8プロセス終了/ポート解放 | 成功 | 成功 |
| 強制終了/process log error | なし | なし |

UI操作ありでは、一覧50件・画像decode・bridge、viewer開閉、学習判定/Undo、タブ離脱/キーボード/復帰focus、専用DB保存後のreloadと次作品を確認し、console/page errorは0。UI操作なしは操作受入を代替せず、実画素gateが省略されない経路として検査した。

primeと独立レビュアーがbefore/after計4全画面を目視し、画像・配置・表示数の保持を確認した。一覧は復帰後のカード1背景に差があり、平均差が閾値内という判定を画素完全一致へ読み替えない。

## 試験と独立レビュー

一時reparentの4回帰は修正前に4件失敗を確認。修正後はcapture後のdetachも追加し、対象4 test fileで**112 passed / 0.47秒**、全体で**329 passed / 8.54秒**。実行時のimport先は専用worktreeのsrc、`pip check`と`git diff --check`も成功した。

故障注入は、変更されたowner、恒久detach、detach後のidentity変更、capture後のDPI変更、別scene/一様画像、連続性のリセット、遅いcapture/PNG保存、topmost変更・復元失敗、既存PNG保護、gate失敗や時間切れ後の通常終了を含む。Linux/Windowsのpytestは合成画像と偽API/時計を使い、実機試験と区別する。

独立レビューで指摘されたowner固定と通常終了予算を修正し、実機診断後の限定再検査も再レビューした。最終コード、112件のXML、実機JSONと4PNG、全体329件の結果について重大な残件なし。CIとPRの統合状態は該当PRのheadとChecksで追跡する。

証拠はignored `artifacts/native-restore-gate-20261022/`。`first/`の不合格、`diagnostic/`の時系列と別条件の診断、`accepted-ui/`・`accepted-plain/`の標準checker結果、`reparent-red.xml`、`targeted-final-pytest.xml`、`integrated-pytest.xml`を分けて保存した。生画像・プロセス情報・ログはGitへ追加しない。

残るWindows受入は100/125/150%など他DPI、別PC、最大化や長時間の最小化/復帰、トレイ/URI等。先行した画面外配置事象の原因調査も残り、この追加検査の成功で段階1全体を完了にはしない。
