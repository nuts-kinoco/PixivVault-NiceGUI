# GPU 作業 7 の検証結果

- 作業ツリーは `S:/Work/PV/gpu-wire-20261122`、ブランチは `gpu-wire`。土台 `254b70ef9bd5cc3893db47514b22ff9fa6d61529`、実装候補 `fe70381f1bab808edb231b8c9f453b7cca9f359c`。
- 設定・サービス・消去・リースの隔離受入は合格。おすすめ適用後の ON→OFF 完全一致、Chromium と実機の受入は保留。

## 原受入との対応

| 項目 | 確認結果 |
| --- | --- |
| 設定 | 旧 sidecar を開き、新規キーの既定値、保存と再読込、未知キー拒否を確認。既定は補正 OFF、実行手動、先読み OFF。アイドルへの明示変更は補正 OFF でもシャドー解析に適用した。 |
| 起動・状態・操作 | 検証済みローカルアカウントに観測を接続。手動開始、一時停止、アイドルへの切替、停止理由、件数・世代・実行先・追加 GET 0 の表示を合成入力と NiceGUI 要素で確認。待機中の GPU 予約は UI スレッドの外で行う。 |
| 削除 | 確認元のアカウントを退役後も照合して `purge_owned()` を呼ぶ。合成データでは壊れた所有済みストアからも消去でき、保存画像・判定 DB・類似解析台帳・設定のバイト列を保持した。 |
| 終了 | 解析中の停止で結果を失効し、CUDA セッション解放、共有リース解放、reader 閉鎖の順を確認。停止タイムアウト後は独立資源を閉じ、未終了ワーカーを参照したまま再試行する。視覚ストアの接続は操作単位で閉じる。 |
| 共有 GPU | 偽 `lease.py` で PV、6 GiB、30 分、待機指定、通常・例外・後継所有者のケースで一度だけトークン付き解放することを確認。共有停止で待機をキャンセルする。CUDA 振り分けで GPU が空くまでは CPU へ切り替えない。CPU 表示・実行は既存起動設定が明示 CPU の場合。 |
| 補正 OFF | 作業 7 単体で世代失効を確認し、学習候補の非空スレートで変更前と同じ順序・枠を確認。作業 6 のおすすめ変更を統合した候補で、ON の適用と OFF の完全復帰を確認する必要がある。 |
| 通信 | 製品の Pixiv 取得経路を追加していない。既存経路の試験と通信棚卸しは合格。サムネイル先読み設定は保存する。追加 GET は承認済み設計どおりフェーズ 3 まで接続しない。 |

## 実行した試験

最終候補の隔離試験は計 136 passed、Chromium は D6 で 1 skipped。
指定の隔離 Python で `-B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"` を使用。TEMP/TMP と basetemp は `S:/Work/_tmp/PV/gpu-wire` に置き、PYTHONPATH は当候補の src と tests、PYTHONIOENCODING は解除した。

| 対象 | 結果 | 原証拠 |
| --- | --- | --- |
| 実装前の設定・gap・入力正規化・リース契約 | 10 failed。未実装インターフェースの失敗を含む | [red](red.txt) |
| リース解放失敗をまたぐ再開始 | 1 failed → 最終作業 7 試験で合格 | [red](red-release-race.txt) |
| 作業 7 の専用試験 | 23 passed | [green](green-final.txt) |
| 視覚学習ストア | 43 passed | [store](store.txt) |
| G4 シャドー、G4 再レビュー回帰 | 22 passed、19 passed | [shadow](shadow-final.txt) / [review](shadow-audit-final.txt) |
| GPU 調停、設定外観 D、スケジューラ r5 | 13 passed、1 passed、8 passed | [GPU](gpu-arbiter.txt) / [D](tone-d.txt) / [r5](scheduler.txt) |
| 通信棚卸し | 7 passed | [P6](network-final.txt) |
| Chromium のキーボード・フォーカス・コントラスト | 1 skipped。D6 フラグを確認し、Chromium は起動していない | [browser](browser-final.txt) |
| 全体 pytest | D6 フラグのため未実行 | [終了時確認](processes-final.txt) |

通信棚卸しの更新は計 5 件。サービス初期化 4 件とシェル初期化 1 件の周辺に視覚学習配線が加わったため、既存呼出し式の一致を照合し、呼出し元関数のハッシュだけを更新した。許可分類と既存ゲートは維持された。

## G4 Medium と CUDA メモリ不足の判定

- (a) 新しい purge 世代を snapshot/invalidate の前に同期して、直後の失敗を gap として保持する。
- (b) 視覚ストアの読込み・書込みで、保存済みまたはブックマーク済み、かつ `revision == 0` の `null` 判定を空文字へ正規化する。Undo の `revision > 0` は `null` のまま保持する。ストアを通らず JSON を直接渡す評価器と純粋 fit 入口は所有範囲外。その入口までの修正は残っている。
- (c) CUDA セッション読込みのメモリ不足分類を共通化した。既存のエラー分類に加え、下表の代表例をモックで照合した。実 GPU で OOM は発生させていない。

| 発生元 | 文言または識別子の要点 | 根拠 |
| --- | --- | --- |
| ORT BFCArena 拡張失敗 | `Failed to allocate memory` と要求バッファサイズ | [BFCArena 実装](https://github.com/microsoft/onnxruntime/blob/main/onnxruntime/core/framework/bfc_arena.cc) / [実際の CUDA エラー報告](https://github.com/microsoft/onnxruntime/issues/14942) |
| ORT BFCArena 容量上限・空きチャンク不足 | `Available memory` と `smaller than requested bytes`、または Extend 後の空きブロック不足 | [BFCArena 実装](https://github.com/microsoft/onnxruntime/blob/main/onnxruntime/core/framework/bfc_arena.cc) |
| CUDA runtime / driver | `cudaErrorMemoryAllocation`、`CUDA_ERROR_OUT_OF_MEMORY`、`out of memory` | [CUDA runtime の列挙値](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-runtime-api/group__CUDART__TYPES.html) / [ORT の cudaMalloc 呼出し](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/providers/cuda/cuda_allocator.cc) |
| cuBLAS / cuDNN | `CUBLAS_STATUS_ALLOC_FAILED`、`CUDNN_STATUS_ALLOC_FAILED` | [cuBLAS](https://docs.nvidia.com/cuda/cublas/) / [cuDNN](https://docs.nvidia.com/deeplearning/cudnn/archives/cudnn-890/api/index.html) |

## 逸脱・残り

- Sol がデータ保全と並行処理を含む差分を独立静的レビューした。旧アカウント側の削除競合、共有停止中の外部待機、再開始時の解放結果、状態読戻しの境界を指摘に沿って更新し、最終候補のソースを再確認した。確認済み blob は services `0518a435`、worker `f5b369a4`、store `85947e5a`、arbiter `29c76f93`、設定画面 `4c8f4541`。Sol の担当は読取りと助言。pytest は作成担当が実行した。
- 先行試験の偽 reader に `close()` がなく、終了検証で一度失敗したためフィクスチャに追加した。テスト閾値を変えていない。
- ネイティブ呼出しが協調停止時間を越えた場合は解放確認待ちが残る。新しい CUDA 呼出しの前にリース期限を検査し、同時に別所有者のトークンで解放しない。実 GPU、実 API、実プロファイルの受入は未確認。
- 次は監督が候補を回収し、作業 6 と対応する候補で補正 ON→OFF と通信差分を確認する。D6 フラグが消えた候補で全体 pytest と Chromium の Escape、フォーカス復帰、ライト・ダークの 4.5:1 を実測する。
- 作業ツリーと原証拠は回収待ち。一時領域の再生成可能な出力は監督の削除候補とした。 [終了時確認](processes-final.txt) / [削除ドライラン](cleanup-dry-run.txt)
