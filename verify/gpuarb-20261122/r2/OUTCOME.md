# G3 第2回 GPU 排他と画像変種

最終候補は 227b1d0522f70c0b93aca278bbfb92ca848e8e7c。基点は af2d539。対象は GPU_LEARNING_INTEGRATION_PLAN.md §3・§4作業3と、rx_g0g3.md の (b) G3。製品コード、試験、通信棚卸しの Git tree はそれぞれ 684bba7765fe5000238ca20f56dd5c062c9e3072、217efdc045b659205f9c2af6ef227cb6da0c5c00、31d5ddfa38705a3bc3d4d857eb11480911c995ff。

G3のコード受入を達成。最終候補の単独全体pytestは失敗・収集エラー0。正式な製品受入は、実GPUと製品への停止配線を検証してから判定する。

## 対応

- High 1 — 通常は優先度順・同順位FIFO。最古の待機要求が8回の取得を待った時点で優先し、連続した高優先度要求の下でも進む。
- High 2 — LocalTagger のCUDA実行時エラーではセッションを破棄し、解放対象として所有登録を維持する。release_all()/release_idle() 後に再ロードできる。恒久的な読み込み・出力不整合には失敗ラッチを維持し、連続再試行を防ぐ。
- High 3 — CPU-only と auto の CPU フォールバックはCUDAリースの外で実行する。CUDAの初回ロードに失敗した後のCPU実行は、協調停止で破棄しない。
- Medium — 解放は全オーナーを試行し、失敗は ExceptionGroup で返す。失敗したオーナーは次の解放で再試行できる。セッション検証失敗時は生成済み参照と元の例外を手放し、CPU再試行より先に解放する。CUDAが候補のときはDLLをプリロードしてからプロバイダーを照会し、autoはロード失敗時にCPUへ移る。
- Medium — GPUリースはCUDAのセッション生成とネイティブ実行に限定する。ファイルのSHA、テンソル生成、ヒット判定はリース外。待ちの間のモデル差替えは取得後の軽量な同一性照合で拒否する。初回ロードと最初の推論の間はアイドル解放を保留する。
- Medium — SimilarityWorker と LocalTagView は協調停止で例外カウントを増やさず、同じ走査を中断する。走査・タグ投影・再走査を同じ停止世代で扱う。停止したLocalTagViewは通常更新で再開せず、周期メタデータ確認と失効を続ける。バックエンド未検証の状態は利用可能として公開しない。
- Low — 画像変種の非昇格参照は ImageCacheNamespace.peek_memory() を使う。TaggerManager は設定に対応するモデルを確定後に停止スコープを取る。前回の未追跡ログ33件は監督削除候補のS:一時領域へ移した。

## 検証と逸脱

| 実行 | 結果 | 原証拠 |
| --- | --- | --- |
| NeonのHighと実装に関するMedium | 修正前12 failed | [red.log](red.log) |
| Sol指摘の追加再現 | 走査・アイドル解放4 failed、待機中の差替・CPU再試行3 failed、自動再開1 failed、利用可能公開・投影停止2 failed、停止中メタデータ1 failed | [走査](red-review-followup.log) · [差替・CPU](red-identity-cpu.log) · [再開](red-automatic-resume.log) · [公開](red-publication.log) · [メタデータ](red-paused-metadata.log) |
| 全体試行で判明した既存回帰 | 初回推論中のモデル無効化、パス変更、モデル差替の3件が失敗。単独で3 failedを再現 | [初回全体試行](full.log) · [単独再現](red-existing-inflight.log) |
| 最終候補の関連回帰 | 113 passed in 42.88s。上の既存回帰と作業3の専用試験を含む | [green-inflight-final.log](green-inflight-final.log) |
| 通信棚卸し | 306箇所、指摘0。サイトのキーとゲート分類は同じためreview.jsonの変更なし | [network.json](network.json) |
| 新規プロセスでのimport | ORTとNumPyを未ロード | [lazy-import.log](lazy-import.log) |
| 最終候補の全体 pytest | 4139 passed、1 failed、3 skipped、3 deselected、37分18秒 | [full-final.log](full-final.log) |
| Windows共有試験の切り分け | 候補の2パラメーターは1 passed・1 failed、同じheadのみは1 failed、隔離基点のheadは1 passed、S:上の別basetempで候補のheadは1 passed | [候補](sharing-retry.log) · [headのみ](sharing-single01.log) · [基点](sharing-baseline.log) · [別basetemp](path-probe.log) |
| 最終候補の全体再試行 | **4140 passed、0 failed、3 skipped、3 deselected**、14 warnings、26分28秒。失敗した共有ケースも同じ単独実行で通過 | [full-repeat.log](full-repeat.log) |
| 残存プロセス | 作業の所有プロセス0。候補の製品コード・試験・棚卸しは実行後に差分0 | [processes-final.txt](processes-final.txt) · [run-state.txt](run-state.txt) |

初期の関連試行では旧CPU失敗状態を再試行する中間差分で進捗が止まった。自身が起動し子プロセスのないインタープリターだけを停止した。CPUの恒久エラーラッチを復元後、3回の関連試験で完走した。停止中メタデータ試験の初回は試験フィクスチャのインデントで収集エラーとなり、修正後に意図したredを確認した。Solは独立読取りとして再現条件と最終の修正箇所を確認した。pytestの実行は実装担当が行った。

d297346の最初の全体試行で、初回のネイティブ実行中に検証済み指紋が未公開という既存操作の回帰が分かった。同じ差分で3件を単独再現後、子プロセスの終了を待って自身の試験インタープリターだけを停止した。初回セッションが実際に検証された時点で指紋を公開し、既に検証済みのモデルはキャッシュ参照で再ロードしないようにした。修正差分はSolが独立再確認し、最終試行前にコミットした。二つの全体試行の間で試験ファイルは同じ。

最終候補の全体試験では、Windows共有を再現する保存世代試験で、cold readerがactive-generations.headの置換時にWinError 5を返した。単独試行ではwarm readerで同じ置換エラーが出た。G3の前後で対象試験・保存世代コードの差分は空で、隔離基点と候補の別試行は合格した。フェンス取得の時系列と環境差の直接原因は未確認。最終候補と試験を固定したまま、別basetempで全体を再試行した。

全体試験は指定Pythonと -B -m pytest -p required_network_guard -k "not dpapi" を使用。PYTHONPATHは作業ツリーのsrc;tests。PYTHONIOENCODINGは解除し、TEMP/TMP、APPDATA、LOCALAPPDATAおよび長いbasetempは S:\Work\_tmp\PV\gpu-arb-20261122-r2 配下。PLAYWRIGHT_BROWSERS_PATH は隔離の前に導入済みChromiumの場所を設定した。製品コードと試験は実行前にコミットし、実行後も同じGit treeであることを照合した。3スキップはWindowsのシンボリックリンク作成条件、3選択外は指定のDPAPI除外。

## 残りと次の作業

実GPUとVRAM実測はG0のリース付き再確認、製品の停止・再開とEMAのプロセス間GPUリースは作業7。画像変種のシャドー配線と追加GET検証は作業4。実機、実Pixiv、実GPUの操作およびpushは行っていない。

試験一時フォルダと前回の未追跡ログ33件は監督の削除候補。作業ツリーは監督による回収・統合まで保持する。[前回のログ移動先と一覧](r1-log-inventory.txt)
