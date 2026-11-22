# GPU 視覚嗜好学習 作業 4 シャドー記録

候補 `fdf44b790533d7f8ccb4acd690625fe09888be9d`、基点 `a466487`、ブランチ `gpu-shadow`。対象は `GPU_LEARNING_INTEGRATION_PLAN.md` §3・§4 作業 4・§6・§8 と、2026-11-22 の引き継ぎ修正。実装と試験の書き手は親 Codex のみ。Sol は独立した読取り検証を担当。

作業 4 の隔離受入を確認した。関連回帰 118 件が合格し、独立読取り検証を完了。全体 pytest は 4,275 件合格、全体実行で一括選択の時間閾値が 1 件未達。順位補正、製品の設定・起動停止配線、実機・実 GPU の受入は後続作業。

## 実装

- 学習 FIFO で確定判定・理由変更・Undo・復元・リセット後に公開世代を失効し、判定リビジョン付きで独立ストアへ追記する。通常の失効ファイルの置換が失敗した場合は、所有確認した代替レコードで失効世代を更新する。イベント記録の失敗は学習操作の成否から隔離し、次回の書込みで欠落を追記する。
- 順位診断は元の枠分けと整列処理を共有する。順位確定時に候補集合・ベース順位・枠・スコア内訳・当時の判定を記録する。表示成功時には実際の待ち行列・枠・画像の観測状態を別イベントに記録する。キャッシュから追い出された場合は表示された画像の変種を不明とする。
- ランキングピンの既存先読みから、取得成功のバイト列と同じ結果の変種を渡す。表示完了側は共有 LRU の順序を変えない `peek_with_variant` を使う。`thumb` と `thumb_fallback` は別キーで保存し、フィットから除外する。
- 既定で起動しない単一ワーカーが明示 CUDA と低優先度の `GpuInferenceArbiter` を使う。共有 GPU 解放後は `start()` で再開するまで新規画像を受け付けず、停止がコミットをまたいだ挿入は封印付きの保留行として退役する。外部 GPU リースの所有は作業 7 のサービス側。
- Windows の復旧削除は所有確認後に世代を失効し、現行世代の読取りが閉じてからファイルを扱う。評価器は同一作品の最新リビジョンで、ベクトル欠損行を含む判定・矛盾・保存状態を集約してから `fit()` に渡す。
- LocalTagView と SimilarityWorker は明示再開を共有 GPU の停止世代に結び付ける。明示 CUDA のローカルタグ・モデル読込 OOM は、原因解消後にタグ推論器の再作成または再起動を要する。モデルが読込済みの段階における協調停止はそのまま再開できる。

## 実測と逸脱

| 実行 | 結果 | 原証拠 |
| --- | --- | --- |
| 実装前の先行試験 | 11 failed、1 passed | [red.txt](red.txt) |
| 最初の関連回帰 | 81 passed、3 failed。類似検索の既存呼出し互換性 2 件と棚卸し 1 件 | [green-first.txt](green-first.txt) |
| 照合と初回修正後 | 87 passed | [focused.txt](focused.txt) |
| Sol の指摘と停止要求順序の再現 | 6 failed、13 passed | [red-review.txt](red-review.txt) |
| 実ピン・失効失敗・再開順序の修正後 | 91 passed | [green-review.txt](green-review.txt) |
| アイドル解放・コミット交差の追加再現 | 2 failed、18 deselected | [red-gpu-fence.txt](red-gpu-fence.txt) |
| 共有 GPU 停止とコミット交差の修正後 | 118 passed | [green-final.txt](green-final.txt) |
| 別ハンドルでの失効代替経路 | 旧候補で Undo 後も旧世代の埋め込み書込みが通り 1 failed | [red-durable-fence.txt](red-durable-fence.txt) |
| 最終候補の関連回帰 | **118 passed**。通常画像と 404 代替で偽 transport の GET 列が一致。候補順位、枠、タイブレークは OFF と一致 | [green-durable-fence.txt](green-durable-fence.txt) |
| 基点との順位比較 | 基点 `a466487` と合成 192 ケースで作品順・枠がすべて一致。診断ありの返り値も同じ | [rank-parity.txt](rank-parity.txt) |
| 通信棚卸し | 306 箇所、指摘 0。追加通信経路 0 | [network-inventory.json](network-inventory.json) |
| 最終候補の全体 pytest | **4,275 passed、1 failed、3 skipped、3 deselected**。36 分 45 秒、14 warnings | [full-candidate.txt](full-candidate.txt) |
| 一括選択 20,000 件の切り分け | 全体実行 1.183 秒。基点 0.719／0.744 秒、候補 0.780／0.699 秒。基点→候補→候補→基点の順で実行し、各回 8 件合格 | [bulk-comparison.txt](bulk-comparison.txt) · [対象コードの差分照合](bulk-fixture-identity.txt) |
| 残存プロセス | 起動した試験プロセス 0。作業ツリーは監督の回収待ち。一時領域の削除をドライラン | [processes-final.txt](processes-final.txt) · [cleanup-dry-run.txt](cleanup-dry-run.txt) |

棚卸しは `_fetch_candidates` の順位計算呼出しと `request_image` の既取得元保持により、関数のハッシュが変わった 3 キーを移行した。既存の GET 入場条件・行先と、ブラウザーに渡す内部メディアパスの式を照合した。元の分類は 2 件が `gated`、1 件が `local`。

`2b23eb3` と `6c5a3a9` の全体試験は、独立読取りでそれぞれ実ランキングピンと共有 GPU 停止、失効ファイルの置換失敗時に旧世代の処理が通る問題が判明し中断した。確認した自身の pytest 子プロセスだけを停止した。最終候補は別の長い basetemp で完走した。 [2b23eb3 の途中経過](full.txt) · [6c5a3a9 の途中経過](full-final.txt) · [プロセスの所有記録](process-ownership.txt)

全体試験は指定の Python で `-B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"` を使用。`PYTHONPATH` は作業ツリーの `src;tests`。`PYTHONIOENCODING` は解除し、TEMP、TMP、APPDATA、LOCALAPPDATA と basetemp は `S:\Work\_tmp\PV\gpu-shadow-20261122` 配下。既存の Playwright ブラウザーを参照する。実 API・実プロファイル・本番ポート・実推論 GPU は使用していない。

## 未達と次の作業

全体実行で `test_w_bulk_performance_20261118.py::test_select_many_targets_has_bounded_rendering[20000]` の 1.0 秒上限だけが未達。件数表示と 400 チップの上限は通過した。基点は `git archive` から S: に隔離して再現を試みた。計測区間の一括選択画面・状態、試験とそのフィクスチャに基点からの差分はなく、隔離再試行の範囲は重なった。全体実行時の遅延の要因は未確定。この時間閾値の未達は監督に引き渡す。

Sol は実ランキングピン、アイドルとコミット交差を含む共有停止、明示再開、単一の失効ファイルの置換失敗時に別ハンドルの旧世代を遮断することを独立読取りで確認した。両方の失効レコードが書けず SQLite だけが書ける場合は、公開済み世代を退役できる。作業 5 では適用直前に現行の判定指紋を照合し、照合できない候補への補正を見送る。

次の担当は、監督がこの候補を回収した後、既存の順位を基準に作業 5 の純粋な補正入口および作業 7 のライフサイクル・共有 GPU リースを割当範囲で検証する。作業ツリーと検証証拠を保持し、監督が確認済みの一時領域を片付ける。
