# GDI画面取得の実装・レビュー・実測

## 対象と目的

画素観測器のPillow screen取得は1回の取得負荷が約200msあり、短い遷移の差を粗く観測していた。
指定した物理client矩形を32bit top-down DIBへ直接コピーする `--capture-mode screen-gdi` を追加した。
既定のwindow方式と既存screen方式は維持し、校正metadataのcapture_modeが異なる測定を拒否する。

新方式はWindows専用。`GetDC` / `CreateCompatibleDC` / `CreateDIBSection` / `BitBlt` / `GdiFlush`の結果を検査し、GDI資源から独立したRGB画像を返す。
呼出元がDPI awarenessとウィンドウ所有権を保証する。既存のPID/birth・位置/寸法・可視/topmost・取得前後9点被覆・一様画像拒否・校正・未完走拒否は両screen方式で共通に通る。
9点間や瞬間的な被覆を完全に検出できるとは主張しない。

## 独立レビューと回帰

担当外のレビューで、SelectObjectによるbitmap復元に失敗した後、選択中のbitmapを先に削除して資源を残す問題を発見した。
復元失敗時はmemory DCを先に破棄してからbitmapを削除する。すべての後処理を試み、失敗を成功画像に変えず報告する。主処理の例外があれば保ち、後処理の問題をnotesへ記録する。

さらに実ctypesのHANDLE型はNULLを `None` として返すため、0だけを使ったfakeでは失敗検出漏れが残った。
`restore_none`回帰を追加して、修正前は独立担当も `46 passed / 1 failed (DID NOT RAISE OSError)` を再現。
修正後はNone/0/HGDI_ERRORのすべてを復元失敗として記録し、同担当が関連2試験ファイルの **47 passed / 0.10秒** を確認した。
24件の初期成功や、NULL修正前の測定値を最終受入へ置き換えない。

最終レビューは新規capture本体・故障注入試験・既存benchmark接続・前後被覆検査の試験の4ファイルを対象とし、重大残件なし。
RGB順序、上下方向、コピー後のbyte独立、仮想画面境界、各GDI失敗と後処理、前後の所有者変更・不正寸法・一様画面を確認した。

## 同一画面での取得比較

2026-10-22 10:48 JST、UI `43d0c64bd7acb6f96f387f9ff3639478417c278f` のexact archiveから構築したonedir/noconsoleを使用。
作品0003の3/3を固定し、物理client1920×1080、DPI168（175%）、同じ画面位置、専用所有プロセスで比較した。
各方式20回、実行順を交互に入れ替え、同じ全clientのRGB画素が20組すべて完全一致した。primeは全画面PNGも確認した。

| 取得方式 | 検査込みp50 | 検査込みp95 | コピーだけのp50 |
|---|---:|---:|---:|
| Pillow screen | 220.079 ms | 250.337 ms | 130.109 ms |
| screen-gdi | 115.816 ms | 120.938 ms | 26.959 ms |

「検査込み」は `OwnedWindow.capture()` 全体、「コピーだけ」は既存の取得前後検査の内側で画像コピーを計った時間。
後者は所有権検査を省いた本番測定値ではなく、取得負荷を分けて観察する補助値である。
これは同じアプリの画面取得方式の比較であり、FletとNiceGUIの性能比較ではない。
約116msの検査込み取得時間がまだ残るため、短い入力遅延や平均FPSの判定には使わない。

最初の試行は専用画面の右端3点に他ウィンドウのPIDを検出し、取得前に拒否した。
専用ウィンドウだけを移動し、同じ検査を通して測定を再開した。拒否した結果を成功値へ含めていない。

## 校正・30遷移・終了

最終実装で作品0003のpage index 2から校正し、primeが `frame-0/1/2.png` の全client画像をそれぞれ確認した。
作品名、1/3→2/3→3/3、異なる合成画像、header/navと常設情報行の表示を確認後、校正承認を記録した。
同じPID/birth・位置・top/input client寸法・DPI・ROI・tool hashを照合し、page2から始める30遷移が10:50 JSTに完走した。

| 指標 | 値 |
|---|---:|
| 完走 | 30/30 |
| 観測p50 | 115.605 ms |
| 観測p95 | 123.451 ms |
| この遷移列のcapture overhead p50（10回） | 92.520 ms |

物理client1920×1080、input client1922×1082、DPI168、ROI(x710,y330,w500,h400)、warm320画像、600ms入力間隔。
これは新方式がnative input→画像観測の経路を通る確認であり、旧UI `3947c95` / Pillow screenの結果と直接比較しない。
取得方式比較20組とページ遷移列は別の試行であり、capture負荷の数値も分けて保持する。

GDI object数は取得比較の前後とも0。計数API自体も別途DC作成→解放で0→1→0となることを確認した。
専用8プロセスはWM_CLOSEで通常終了し、app/debug両ポートを解放、process logの既知失敗文字列は0。
元UI7ファイルhashと元database.py hashは一致、constructor以外115メソッドAST一致も維持した。
専用worktreeの明示PYTHONPATHによる統合pytestは **210 passed / 7.40秒**。
UIのみのarchive132件、前単位の統合180件、今回の限定47件とは対象範囲が異なる。

## 証拠と残ゲート

生データはignoredの `artifacts/native-gdi-20261022-01/` に保持する。
最終値は `capture-comparison.json`、NULL修正前は `capture-comparison-before-null-fix.json` と分ける。
`screen-points.json` に被覆拒否、`native-window.json` に現在の専用プロセスと物理寸法、`session-gdi.json` にUI SHA・計測ソースhash・fixture・DPI・ROIを保存した。
`native-gdi/calibration.json` と3全frame、`calibration-review.json`、`native-gdi/result.json` が校正/30遷移、`resource-counter-check.json` が計数API、`finish-check.json` が通常終了、`pytest.xml` が210件の証拠。
計測器hashは `1624bcdd62828faa30cf157d5bc21db3d955775d525014fbe8ad4912267a93d6`、GDI本体hashは `37005f8dfbb4d24372161ca9b8fe883ef7566f51f86e0df1a3399bf22c2d244a`。
関連4文書も実装担当とは別の担当が上記JSON/XMLと照合し、数値・SHA/hash・試験範囲・未受入の区別に重大な齟齬なしと承認した。

フェーズ0の原shell/画像面積/配布条件の同等化、Fletとの同条件再比較、cold/warmと順序の分離は未完。
この変更は測定方式の受入単位であり、フェーズ0全体や製品の高速化を完了扱いにしない。
