# GPU 視覚嗜好学習 フェーズ1 作業3

- 作業ツリーは S:\Work\PV\gpu-arb-20261122、ブランチは gpu-arb。
- 基点は 6cb13ae5202069ce61d03f447a478e9cf3571862、実装候補は 80d0fedc47316137f46be6c5d538107813d284bd。製品コードの Git tree は 08cdce3069faf493892cc89f59ad5115a703b7b4。
- 原受入は GPU_LEARNING_INTEGRATION_PLAN.md §1、§3、§4 作業3、2026-11-22 の担当指示。設計書は監督側の原本と候補内で SHA-256 一致を確認した。
- 文脈管理は同じ会話で継続。監督のセッション操作や作業の再起動は行っていない。
- 作業3の変種・プロセス内排他・停止解放は合成試験と独立静的レビューで成立。全体回帰は環境修正後の再試験と照合済み。実 GPU・製品への停止配線を含む正式受入は保留。

## 実装と受入範囲

- remote_media.py は取得結果と実変種を一緒にキャッシュし、404 の一覧サムネイル代替、通常 view、192px サムネイル、うごイラ GIF、原本、ノベル表紙を区別する。peek_with_variant() はメモリのみを確認し、共有 LRU、ディスク、偽クライアントの呼出回数を変えない。get_with_variant() は通常取得の結果をそのまま作業4へ渡せる。get() と peek() の返り値は ImageBlob。
- gpu_arbiter.py は同一プロセスで CUDA セッション生成と推論を排他し、待機中は優先度順と同順位 FIFO で処理する。release_idle() は非稼働時、release_all() は待機・実行中の要求を協調停止して実行中のネイティブ呼出が戻った後にセッションを解放する。
- egara_inference.py と local_tagger.py は同じ既定アービターを使う。再ロード可能な release_session()、任意依存の遅延 import、Egara の明示 CUDA 失敗時の停止と既定 auto の CPU フォールバックを実装した。LocalTagger は CUDA の DLL をプリロードし、計測前の既定として Egara 8 GiB、LocalTagger 2 GiB の ORT アリーナ上限を指定する。実 VRAM 使用量の上限ではない。
- tools/p6_network_review.json は関数指紋が変わった6箇所と capability 参照の行番号2箇所を更新した。取得経路の判定は従来のゲートと同じ。順位付けと設定/UI の配線は後続作業の所有範囲。

## 検証

| 検証 | 実測 | 原ログ |
| --- | --- | --- |
| 実装前の専用ケース | 18 failed | [red.log](red.log) |
| Sol の指摘を再現 | 要求途中1 failed（停止後に CUDA が再ロードされ、合計2回）、テンソル準備1 failed、DLL 準備2 failed | [red-manager-real-path.log](red-manager-real-path.log) · [red-tensor.log](red-tensor.log) · [red-preload.log](red-preload.log) |
| 専用22件と既存関連17件 | 39 passed in 14.91s。製品入口の再現強化後も39 passed in 12.66s | [green-final-boundaries.log](green-final-boundaries.log) · [green-rereview.log](green-rereview.log) |
| 静的な通信棚卸し | 306箇所、指摘0 | [network.json](network.json) |
| 新規 Python プロセスでの import | ORT、NumPy とも未ロード | [lazy-import.log](lazy-import.log) |
| Chromium の環境差の再現・復旧 | 隔離先で起動失敗を再現。導入済み Chromium の場所を明示して関連52 passed in 152.50s | [browser-path-repro.log](browser-path-repro.log) · [browser-path-retry.log](browser-path-retry.log) |
| 全体 pytest | 4107 passed、1 failed、13 skipped、3 deselected、14 warnings、41分17秒 | [full-final.log](full-final.log) |
| 全体実行と再試験の照合 | 4121選択中4118件に合格証拠。シンボリックリンク作成条件の3件は skip | [coverage-recheck.txt](coverage-recheck.txt) |
| 終了後のプロセス確認 | 起動した試験プロセスの残存0 | [processes-final.txt](processes-final.txt) |

試験は指定 Python の -B -m pytest -p required_network_guard -k "not dpapi" を使った。PYTHONPATH は候補の src;tests。TEMP/TMP、APPDATA、LOCALAPPDATA、各 basetemp は S:\Work\_tmp\PV\gpu-arb-20261122 配下に固定した。PYTHONIOENCODING は設定せず、キャッシュプラグインを無効にした。初回の全体試行と最終試行は別の basetemp を使用。

## 逸脱と対応

- 初回の実装後、専用試験のモックがこの venv に無い NumPy を要求した。モックの出力を標準ライブラリだけで作るよう修正した。通信棚卸しは6箇所の現行のゲートを照合して指紋を更新した。期待値は維持した。[green-initial.log](green-initial.log)
- Sol が独立静的レビューで、TaggerManager の工程間停止と、テンソル生成・DLL プリロード後の停止境界を指摘した。再現で失敗を確認してから修正し、再レビューで指摘の解消を確認した。試験実行は実装担当が行った。
- 工程間停止の最初の専用試験は、擬似事前検証が製品の利用可能状態を設定していなかった。既存の `current_fingerprint()` を通す試験に置き換え、基点の TaggerManager のメソッドだけを隔離試験へ取り込んで再ロード2回を実測した。最終全体試験の開始後はこの試験だけを強化した。製品コードと既存試験は全体実行時の候補と同一。
- レビュー修正前に始めた全体試行は、最終候補を検証するため、所有を確認した試験用インタープリターだけを中止した。中止時に子プロセスはなく、ランチャーも終了した。[full.process.txt](full.process.txt)
- S: に隔離した `LOCALAPPDATA` を Playwright が既定のブラウザー探索にも使ったため、全体試験のキュー描画ケースは Chromium の起動前に失敗した。プロファイルと一時領域を S: に保ち、`PLAYWRIGHT_BROWSERS_PATH` で導入済みブラウザーを読むよう補正して、ブラウザー依存の既存6ファイルを同じ候補で再実行した。
- 全体実行で Chromium の探索先に影響された1失敗・10スキップは、補正後の再試験で通った。全体実行と再試験のケースを照合し、同一条件での失敗ゼロの全体単独ログは未取得。残り3スキップの理由はシンボリックリンク作成条件。

## 残りと次の作業

- 作業4は既存取得の成功結果に付く変種を受け渡し、シャドー配線後に追加 GET 0 を確認できる。
- 作業7は停止操作とアプリ終了の配線、および EMA 側のプロセス間 GPU リースとの接続を担当する。実モデルの CUDA 実行、VRAM 解放の実測、実 GPU 上の CPU/CUDA 一致は G0 のリース付き検証が必要。
- KEEP は実装コミットとこのディレクトリの検証証拠。S:\Work\_tmp\PV\gpu-arb-20261122 の試験生成物は DELETE 候補。専用の basetemp と試験プラグインが対象で、所有した試験プロセスは0。作業ツリーは監督の回収・統合まで保持する。削除実行は監督担当。実 GPU、実機アプリ、実 Pixiv の操作は行っていない。GitHub に push していない。
