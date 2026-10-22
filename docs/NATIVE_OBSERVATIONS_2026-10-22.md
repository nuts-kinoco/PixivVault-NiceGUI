# 隔離ビューアのnative画素観測 — 2026-10-22

## 判定

両backendで、作品0003の3ページを全画面画像で確認した校正と、warm条件の30遷移が完走した。**計測器による限定観測は成立したが、製品全体の速度改善・メモリ改善の受入は未完**。元shell、画像表示面積、配布方式の差があり、画面取得負荷も大きい。改善率へ読み替えない。

NiceGUIは `3947c9543b1f839ccbc9d702247470f5d655414f` だけをarchiveして作ったonedir/noconsole exe。旧Fletは原UI7ファイルの隔離コピー、FakeClient/FakeDB、専用Flet 0.85.3環境。元のPixivVaultは起動せず、実DB・実画像・Cookie・外部APIを使用していない。

## 条件と指標

| 条件 | 記録 |
|---|---|
| データ | 架空120作品、thumbとviewの合計320画像。両backendのPNGバイト一致を確認 |
| fixture SHA-256 | `f1406aa85562b6b1b477d71d236bf175433dcb35d70796f1957018b4f56b3524` |
| 対象 | 作品0003、640×427 PNGのページ0/1/2。詳細は閉じた状態 |
| top client | 両者1920×1080物理px、同じprimary display、168 DPI＝175% |
| input client | Flet 1920×1080、WebView child 1922×1082。別々に記録しボタン中心を指定 |
| キャッシュ | 全320画像を事前取得、3ページを表示済みのwarm。coldは未測定 |
| 入力 | 専用子HWNDへのWM_MOUSEMOVE / DOWN / UP。入力間隔600ms |
| 取得 | Pillow 13のscreen bbox。topmost・可視・寸法・PID作成時刻・9点所有者を前後確認 |
| ROI | client原点からx=710,y=330,w=500,h=400。平均RGB差閾値12、期待テンプレとの差1以下 |
| 順序 | page2で開始、0へ戻し、0→1→2→1→0の方向を30回観測 |
| 計測時計 | 最初のnative message直前から、期待するROI画素を取得した時点まで。入力前待機・事前検証は除外、captureと取得時検証は含む |
| 計測器SHA-256 | `62f648573d407d54ee7add1380e50cc70c6a5d53354a76717a9804eb89933969` |

primeが両backendの`frame-0/1/2.png`を開き、作品名、1/3・2/3・3/3、色パターン、画像と情報行の可視を確認してから測定を承認した。ROIだけの校正ではない。PID/HWND・作成時刻・全frameとROIのhash・各サンプルはignored artifactに保存した。

## 観測結果

| backend | 成功 | p50 | p95 | capture overhead中央値 |
|---|---:|---:|---:|---:|
| 隔離Flet | 30/30 | 329.053ms | 432.402ms | 194.688ms |
| NiceGUI / pywebview | 30/30 | 209.560ms | 227.204ms | 196.736ms |

Fletの観測値は約210〜244msと414〜463msに分かれる。p50の329msは中央2値の補間で、典型的な1操作が329msだったという意味ではない。約195msの取得周期で1枚目・2枚目に変化を捉える差が大きく、Native側もほぼ1回の取得時間に近い。capture中央値を単純に引いてアプリの応答時間を推定しない。平均FPSでも一般的な入力遅延でもない。交互順序・複数セッション・system負荷固定は行っていない。

画素パターンと画像そのものは同一だが、Fletのtab paddingと新版のviewport overlayでは表示画像の面積・余白が異なる。Flet側は原製品header/navを省略している。この差と配布方式差を解消する前に、上表から製品の改善率を算出しない。

## プロセスメモリの参考観測

30遷移後、各backendのrootと観測子プロセスについてPID/作成時刻を照合し、`K32GetProcessMemoryInfo`を毎回呼んで5回採取した中央値。各回の間隔は0.2秒。初期snapshotのプロセス集合を使うため、その後新しく生成される子の捕捉を保証する長時間計測ではない。

| backend | 観測プロセス数 | private committed bytesの合計 | working setの合計 |
|---|---:|---:|---:|
| 隔離Flet | 4 | 376.25 MiB | 253.16 MiB |
| NiceGUI / pywebview | 8 | 578.11 MiB | 753.57 MiB |

この観測では新版の使用量が大きい。GPU割当は含めず、working set合計には共有ページの重複がありうるため、PC全体の物理メモリ消費量とは扱わない。Fletの5回が同値だったこともそのまま保持した。helperは各回新しい構造体へOS関数の結果を書き、同じmemory snapshotを再利用する経路はない。ただし、この短い観測で安定性やリーク不存在は証明しない。配布方式・shell・プロセス数が違い、製品全体のメモリ優劣は未受入。

## 失敗試行と独立レビュー

- 最初のwindow capture方式ではWebViewが全白となり、校正できなかった。0ms/600ms双方の失敗を保持し、入力取りこぼしが原因だったとは結論づけない。
- 間隔なしFlet試行は6遷移目でtimeout。成功した先頭サンプルだけで合格にしない。計測器は未完走時のp50/p95をnullにする。
- screen方式の初回WebView校正では他ウィンドウの被覆を所有PID照合で拒否。専用ウィンドウを再度topmostにした後に校正を実施した。被覆した画面を測定値へ採用していない。9点検査は未検査領域・瞬間被覆の完全な証明ではない。
- 実装と別の担当がscreen方式のWin32署名、所有者・寸法・位置・校正照合、失敗集約、両測定値と解釈を独立レビューした。計測器の限定試験17件、旧Flet隔離ツールの限定試験10件に成功。
- 測定後は専用両ウィンドウのtopmostを元へ戻し、WM_CLOSEで通常終了。Fletの観測4プロセス、新版の観測8プロセスとも残存なし。別の既存アプリ・ブラウザは終了させていない。

## 証拠と次の受入

主な証拠は `artifacts/native-ui-20261022-02/` の `session-screen.json`、`calibration-review.json`、`flet-screen/`、`native-screen/`、`memory.json`、`native-acceptance.json`。先行失敗は `artifacts/native-a5d42aa-20261022-01/{flet,native,native-paced,diag-worker2-results.json}` と実行ログ。生の画像・ログ・PID情報はgitへ追加しない。

この観測後、UI `43d0c64` でviewerのタブ内境界・タブ切替/フォーカスを修正し、ブラウザ2寸法とDPI175%のnativeで受入した。上表は修正前3947c95での実測であり、新UIの性能値へ転記しない。
次は元shellを含む表示面積と配布条件の一致、取得負荷を下げた計測方法の再校正、cold/warm・測定順序を分けた比較である。現在の記録はその基準となる限定観測であり、段階3の性能合格や既定アプリの置換を承認するものではない。
