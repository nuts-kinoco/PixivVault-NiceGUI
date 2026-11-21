# 定期タイマーの起動・保留・解除

実装前に `fix-sched-startup` の `b01b652c70a072400edd1cedb81924795fbe18fe` を照合した。表の `True` はネイティブタイマーの起動保留、`False` は保留なし。起動済みの停止管理は `closing` と runner の停止イベントが担う。

| 経路 | 現行の遷移と根拠 | 修正後に検証する遷移 |
|---|---|---|
| CLI・非認証連動モード、管理スケジューラを開く | `False` のまま、`enable_managed_scheduler` で起動。既存 ledger があると同じものを返す。`services.py:3345,3425` | 同じ。一度だけ起動。構成済み画面では開くボタン無効 |
| ネイティブ構成、画面接続前、認証進行中 | `register_pages` が `auth_stop_required` を渡して `True`。`enable_managed_scheduler` は ledger と runner を作るがスレッドは起こさない。`bootstrap.py:727,765`、`ui/shell.py:268` | `True` を維持。既存の期限切れ枠も取得しない |
| 起動時の認証成功、初回・期限超過のフォロー同期成功 | `refresh_auth_status` → `after_confirmed` → コミット後の `_signal_following_ready`。一致すれば `True → False`。`native_startup.py:435`、`account_followup.py:119–161` | 同一所有者と現在の起動時 Cookie が有効なら `True → False`。繰り返してもスレッドは1本 |
| 同一所有者の同期が24時間以内 | 同期を省略し、コミット済み所有者で `True → False`。`account_followup.py:144–161` | 同じ。追加のフォロー取得は0件 |
| 認証失敗・未設定、初回同期に失敗 | 認証成功または同じ所有者のコミットがなく `True → True`。`native_startup.py:396–435`、`account_followup.py:144–161,228–261` | 同じ。次の回復経路を受け付ける。既に同一所有者の保存済み一覧があれば、期限超過の更新に失敗しても所有者を確認して使える |
| 手動「Pixivから同期」成功・失敗・取消 | `FollowingSyncView._run_sync` が稼働中リーダーで本人確認し `store.sync`。コミット後に解除へ到達せず `True` が残る。失敗・取消も `True`。`ui/following_sync_view.py:209–235,261–310` | コミット済み結果と現在の稼働中リーダーの確認が一致すれば `True → False`。コミット未成立、旧リーダー、別所有者、再起動待ちは保留。画面切替後でもコミットの成否で判断 |
| 設定の「選択したCookieの認証を確認」 | `NativeProfileView.check_auth` → `check_authentication`。ヘッダーを更新するが `after_confirmed` を通らず `True` のまま。`native_startup.py:166–176,224–244`、`ui/native_profile_view.py:371` | 保存・起動済みの Cookie を検証した場合だけフォロー確認経路へ。保存済み所有者が同じか同期が成功すれば `True → False`。未保存の選択、認証失敗、別アカウントの確認待ちは解除しない |
| Cookie 保存・拡張機能からの受領 | `on_saved` / `_extension_cookie_received` → `request_cookie_check` → `refresh_auth_status`。同一所有者なら旧リーダーでも `True → False` になり得る。`ui/native_profile_view.py:328–341`、`services.py:1189`、`native_startup.py:451–465` | 起動中の Cookie の明示的な再確認は通常の回復と同じ。差し替え・解除の再起動待ちは保留し、稼働済み runner も旧 Cookie で新規受付を開始しない |
| ブラウザからの Cookie 自動抽出、通信中の有効期限延長、次回起動 | `adopt_browser_cookies` の `adopted` 後、新認証情報でフォロー完了を通知するため旧リーダーでも `True → False` の恐れ。`native_startup.py:327–377,414–435` | 差し替え時は `True` を維持。既存の `cookie_replaced_since_launch` が認識する有効期限延長は従来どおり。新しい Cookie で再起動後に `True → False` |
| 別アカウントの検出、確認待ち、キャンセル | `after_confirmed` は確認を表示して戻る。取消時も `True → True`。`account_followup.py:126–138,169–174` | 同じ。別アカウントの台帳や履歴を使わない |
| 別アカウントへの切替確認 | 確認と所有者付き消去後、同期成功で `True → False`。同期失敗で `True` が残り、後続の手動同期でも解除されない。定期履歴はフォロー台帳とは別。`account_followup.py:176–207`、`managed_scheduler.py:213,478` | コミット成功で解除。失敗後の手動同期や再認証も他の回復経路に合流。別アカウントの定期履歴は引き続き非表示・既存データ保持 |
| 定期チェック画面、稼働中リーダーの未確認 | ヘッダー・フォロー確認は一時リーダー。`False` でも `_verified_identity` 未設定なら画面はロックされたまま。`scheduler_view.py:195–200,217–241`、`managed_scheduler.py:164–175` | 明示的な認証確認を同じ取得停止ゲート経由で行い、同一所有者のみ画面を復旧。確認不能・再起動待ち・別履歴の保護を維持 |
| 期限枠・保留済みバックログ・ビジー再試行、手動「今すぐチェック」 | `False` の runner が期限枠とバックログを処理。自動再試行の3秒間も dispatch lock を保持。手動は同じ lock を使う。受付時ビジーのログに工程名がない。`managed_scheduler.py:1380–1452`、`services.py:3368–3422`、`scheduler_view.py:575` | 再起動待ちを確認してから自動受付。3秒はロック外で待ち、再取得後に停止と前提を再検査。受付時ビジーは工程を記録し再試行しない |
| 終了、解除との競合 | `close` が `_managed_scheduler_lock` 内で `closing` を設定し、その後 runner を停止して資源を閉じる。保留ならスレッドなし、解除済みなら join。解除が遅れても起動しない。`services.py:3137–3174,3437–3444` | 同じ。両順序で確認。待ちの間に停止要求が届けば2回目を送らない |

元 Flet 版は `S:/Projects/PixivVault/main.py:375–376` でワーカーを起こし、`scheduler.py:56–88` の周期は初回失敗後も進む。NiceGUI では同一アカウントの確認が後から成立した時点で再起動なしに同等の周期へ復帰させる。

R5 の原報告は「未紐づけ」の表示を確認済み。稼働中リーダーを事前に確認した操作は記録から確定しない。R6 のヘッダー認証成功と稼働中リーダー未確認の組み合わせはコード上で成立する。R6 当日の例外型は引き続き不明。

## 実装後の照合

- 起動・保存通知・自動抽出は既存の `refresh_auth_status` から `AccountFollowup` へ進む。設定の明示認証は、保存済みかつ起動時の資格情報を確認できたときに同じ処理を別タスクで進める。手動同期は、稼働中リーダーでの確認とフォロー台帳のコミットの両方を終えてから共通の解除を呼ぶ。
- 共通の解除は `_managed_scheduler_lock` の内側で終了、再起動待ち、同じフォロー台帳とその最新の所有者を確認する。稼働中リーダー由来の通知では、リーダーが現在も同じで、その確認結果も一致することを条件にした。解除済みや終了中の通知は起動処理を実行しない。
- Cookie差し替え前に起動済みなら、起動保留フラグは `False` のまま。runner が新しい期限枠・保留枠の取得を止め、確保直後の差し替えは受付前に再確認する。実行中の通信には既存のプロファイルと取得停止ゲートの照合が働く。
- 定期チェック画面のボタンは明示操作時のみ、既存の取得停止ゲートを通して稼働中リーダーを確認する。確認後の☆一覧、設定保存、今すぐチェックの表示は既存の所有者確認を通る。別アカウントの履歴の保護と「未紐づけ」の表示条件は従来どおり。
- フォロー同期・認証・Cookie再起動待ち・UIのロック・受付工程の各指摘で red を確認した。24時間以内の同期省略、切替キャンセルと確認、終了競合の4ケースは修正前から成立し、回帰試験として追加した。[red-final.log](red-final.log)
