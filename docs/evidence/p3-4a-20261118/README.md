# P3-4a 学習タブ前半の検証記録（2026-11-18）

対象: `feat-z-learning-a`。製品コードの検証対象は `fa737f6`、UI スモークの終了処理は `d6f84ae`。実データ・実 Cookie・実 Pixiv は使用していない。`pv-dev-baseline` のランキング等を取り込み済み。

| 台帳 | 候補の根拠と主な試験 | 状態 |
|---|---|---|
| FL-I05 | `ui/learning_toast.py:88`, `ui/learning_view.py:844`, `learning_repository.py:130`。`test_no_reason_defaults_to_tag_only`, `test_style_reason_moves_author_not_tags`, `test_genre_reason_keeps_tag_hate_and_not_author`, `test_reason_update_recomputes_consecutive_skips_after_history_reorders`, `test_failed_reason_commit_leaves_undo_usable` | 隔離テストと Sol 独立検証済み。実機受入は未実施 |
| FL-I06 | `ui/learning_toast.py:13,73`, `ui/learning_view.py:739,844`。`test_toast_seconds_follow_acceptance`, `test_last_candidate_undo_buttons_target_empty_current_card`, `test_reset_during_slow_rerank_does_not_restore_old_count` | 隔離テストと Sol 独立検証済み。実機受入は未実施 |
| FL-I12 | `ui/learning_view.py:1387,1403`。`test_release_uses_the_change_event_value_not_a_stale_model_value`, `test_keys_on_settings_slider_do_not_judge`、実ブラウザのキー操作と設定再読込 | 隔離検証済み。元画面との並置・実機受入は未実施 |
| FL-Y07 | `ui/learning_view.py:173,1096`、`test_style_frame_candidate_shows_new_style_badge`, `test_subject_frame_candidate_shows_unknown_subject_badge` | 静的比較と隔離テスト済み。実機受入は未実施 |
| FL-Y08 | `ui/learning_view.py:826,832`, `ui/learning_reset.py:145`。`test_learning_reset_clears_the_count`, `test_reset_during_slow_rerank_does_not_restore_old_count` | 隔離テストと Sol 独立検証済み。実機受入は未実施 |
| FL-Y10 | `ui/learning_toast.py:98`, `tests/test_z_learning_heavy_notice_20261118.py` の保存失敗・再試行・補充・初回失敗 | 隔離テストと Sol 独立検証済み。実機受入は未実施 |

Sol は `fa737f6` までの I05/I06/Y08/Y10 をネットワークガード付きで独立実行し、44 passed。候補が最後の1件の場合のトースト・常設ボタン・Ctrl+Z のサーバー側 Undo と、別セッションによる DB 読み戻しを別途確認した。

`ui-smoke-summary.json` と `ui-smoke.log` は 14画面×2サイズ×2テーマの結果。56条件 PASS、外部接続試行 0、ブラウザ外部リクエスト 0。偽クライアント呼び出しのうち起動時5件と学習・ランキング初回取得は既存の許容条件内。`keyboard.log` は実コンポーネントをブラウザで操作した結果、`slider.log` は隔離製品画面で設定を閉じて開き直した結果。ライト配色は半透明の通知背景を黒・白の両端で合成し、文字 15.68〜17.10:1、補助文字 7.44〜8.12:1、チップ 15.12:1。

全体テストは `-p required_network_guard -k "not dpapi"`、TEMP/TMP は wt-z 内の隔離フォルダーで実施し、2374 passed、2 skipped、3 deselected、失敗0（`full-final.log`）。2 skips はシンボリックリンクの権限・利用不可。改行整理後の3試験ファイルは別途 36 passed（`post-eol.log`）。

逸脱: 元の Undo 帯は4秒だが、今回の明示受入の5秒を採用。初回スモークは全56画面条件とネットワーク検査が通った一方、隔離サーバーの終了タイムアウトで総合 FAIL。同期エンドポイントを async に変更して再実行し PASS。途中候補の全体テストは 2373 passed、2 skipped、3 deselected、失敗0だったが、実行中に候補を更新したため最終候補の結果には使わない。

Y08 は `9d10c87` と `e004d2a` の両方が必要。前者だけでは呼び出し先を含まない。後続の `69faded` で一時的に文字化けした docstring を復元済み。最終全体テストの開始後に `8202867` で試験3ファイルの混在改行のみを既存 LF に統一した。`git diff --ignore-space-at-eol d6f84ae 8202867 -- tests/` が空であることを確認し、製品コードは変更していない。

台帳の正式な「有」は実プロファイルでの到達確認を含むため、隔離検証だけでは変更していない。P3-4a の作成担当以外による原受入の出口判定、元画面との並置および許可された別担当の実機確認が残る。
