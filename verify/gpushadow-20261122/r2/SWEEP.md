# 世代失効・復旧経路の棚卸し

実装前の統合点 `d6a9f5e`。統合元 `pv-dev-baseline` は `7c57e15`。Neon 指摘 #1、#4、#5、#7、#10 を同じ失効契約で確認する。

| 経路 | 失効の宣言 | epoch の前進 | 進行中の書き込みの拒否 | 試験 |
| --- | --- | --- | --- | --- |
| `invalidate` | 判定・停止等の成功境界 | 制御レコードを更新 | 読取り・書込みの旧世代照合 | 判定 FIFO と旧世代公開 |
| `_declare_revocation` | 所有・現状確認後に制御レコードを置換 | 主／代替の更新 | 両制御入力を合わせて照合 | 主ファイル単独障害、構造不正 |
| DB 退役フォールバック | 両制御書込み失敗後に SQLite で退役 | 実装前は更新なし | 実装前は古い epoch の発行・挿入を許す | 別ハンドルの進行中発行と埋め込みの競合 |
| `advance_generation` | 世代の発行のみ | 受理した epoch に結び付ける | ロック取得後・バインド時・返却前に照合 | 失効をまたぐ発行 |
| `put_candidate_embedding` | 内容由来の埋め込み記録 | 受理した epoch を封印 | 書込み前後と遅延回収で照合 | 失効および共有停止をまたぐコミット |
| `publish` | 現行世代だけを公開 | 発行時の柵を使用 | トランザクション前後で確認 | Undo 後の旧世代公開拒否 |
| `purge_owned` | DB 所有確認を完了して失効 | 削除より先に更新 | Windows の現行読取りを排出後に削除 | 所有不一致、読取りとの競合 |
| ワーカー完了 | 個別停止・推論失敗を失効へ反映 | ストアの失効経路と同じ | 実行世代と GPU 停止状態をコミット境界で照合 | 停止とコミットの競合、待機中の個別停止 |
| `arbiter.release_all` | GPU 停止世代を進める | 実装前はアイドル監視の次回起床でストアを失効 | 実装前は繰返し確認まで旧世代可視 | 共有停止の戻り直後に旧世代照合 |

## 修正後の不変条件

ジョブの入力柵は `(制御 epoch, SQLite の世代番号)`。制御ファイルを更新できる場合は新しい値に置き換え、更新済みの別ファイルは保持する。制御ファイル両方の I/O エラーでは、所有を検証した SQLite の退役トランザクションで永続の世代番号を増やす。どの既存書込みも退役後に以前の組を使って受理されることはない。物理 purge は DB の同一性も変わる。制御レコードの所有・構造に失敗した場合は I/O フォールバックを行わず、操作を失敗として返す。

| 経路 | 失効の宣言 | epoch の前進 | 進行中の書き込みの拒否 | 試験 |
| --- | --- | --- | --- | --- |
| `invalidate` | 判定確定境界、個別・共有停止 | 主、代替、DB 退役の順に使用 | 旧制御値または旧 DB 番号を拒否 | `test_committed_notifications_and_generation_invalidation` |
| `_declare_revocation` | 所有確認後 | 主／代替を新しい値に更新。検証失敗は拒否 | 書込み前・コミット前の制御値照合 | `test_revocation_replace_failure_cannot_leave_old_model_visible` |
| DB 退役フォールバック | 制御レコード両方への I/O 失敗 | DB 番号を同じトランザクションで増加 | 別ハンドル・再接続から旧ジョブを拒否 | `test_both_control_replacements_fail_while_fit_and_embedding_are_pending` |
| `advance_generation` | 読取りの前後で制御値確認。ラベル読取り前に取得した入力柵を指定可能 | 書込み時に DB 番号を増加 | 取得・書込み・バインド・返却の境界で照合 | `test_normal_revocation_cannot_split_job_fence_capture`、`test_database_retirement_also_fences_returned_generation_results` |
| `put_candidate_embedding` | ジョブの入力柵と GPU の状態 | 自身は新しいモデル世代を発行しない | 入力柵をロック内で確認。コミットをまたぐ拒否は保留封印を残して回収 | `test_rejected_embedding_seal_survives_a_failed_cleanup`、`test_shared_release_crossing_shadow_commit_retires_the_pending_row` |
| `publish` と読取り | 発行済み世代だけ受理 | 発行時の制御柵と DB 番号を利用 | 公開・返却の直前に DB の現行世代も照合 | `test_database_retirement_also_fences_returned_generation_results` |
| `purge_owned` と DB 内の `purge` | 所有の確認後に操作 | 制御値を更新。DB 内 purge は画像・イベント受理時の削除専用 epoch も変更 | 物理 purge は Windows 読取りを抜き、DB 内 purge は旧キューの再投入を拒否 | `test_recovery_purge_drains_current_reader_before_unlinking`、`test_in_place_purge_discards_events_waiting_to_append`、`test_queued_image_cannot_repopulate_visual_data_after_purge` |
| ワーカー完了 | 個別停止、推論失敗を失効へ反映 | ストアと同じ経路 | GPU 待機は個別トークンで取消。所有者の失効失敗は停止後に呼出し元へ返す | `test_shadow_stop_cancels_waiter_without_releasing_another_owner` |
| `arbiter.release_all` | GPU 停止世代を進め、レコード失効を同期通知 | GPU 停止とストアの両方を前進 | ワーカー起床や状態照会を待たず、共有停止の戻り時点で旧世代は無効 | `test_idle_release_publishes_fence_before_return_without_worker_wakeup` |

制御ファイルも SQLite も更新できない状況は失効未確認として扱う。失効を確認できない復旧操作は終了を返さない。リリースで補正を適用する際は、現在の判定指紋との照合も設計どおり行う。
