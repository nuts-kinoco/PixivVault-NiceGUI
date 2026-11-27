`S:\Work\PV\h2-20261128` の `git diff f5320744..74cdafbe -- src tests tools` に対する読取専用独立レビュー結果です（**High 2 件 / Medium 3 件**）。

---

### 1. [High] 401 確認 GET 中の通信失敗・タイムアウト等で共有 `inflight` が残留し、認証確認も送信中回復も両方不能になる

- **ファイルと行**:
  - [managed_network_stop_auth_recovery.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L90-L113)
  - 関連: [managed_network_stop_shared_scope.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L178-L187), [managed_network_stop_shared_scope.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L304-L310)
- **再現条件**:
  1. 局所または共有が 401 停止の状態で「選択したCookieの認証を確認」（`check_selected_authentication`）を実行する。
  2. `recover_draft_auth_401` 内の `confirm()` が `intent = shared._begin_inflight()` で `.pixivvault-user-stop-inflight.v1` を作成した直後、`raw_transport`（1 回目の `/` または 2 回目の `/ajax/user/extra`）が一時的なネットワーク断・DNS 失敗・タイムアウト・応答不正・クローズ等で非 HTTP の `PixivReadError`（`TransportFailed`, `ReadDeadlineExceeded`, `ResponseRejected`, `ResponseLimitExceeded`, `ClientClosed` 等）を送出する。
  3. `confirm()` は `except HTTPStatusRejected:` しか捕捉していないため（通常の `ManagedUserNetworkStopGate.request()` にある `except PixivReadError:` の後始末が無い）、`shared._finish_inflight(intent)` が呼ばれず、`shared` が `blocked=1, status=401` のまま `.pixivvault-user-stop-inflight.v1` がディスクに残る。
  4. 以後、「選択したCookieの認証を確認」を押すと [managed_network_stop_auth_recovery.py:66](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L66) の `shared._no_inflight()` が `SharedGetOutcomeUncertain`（`uncertain`：「『取得停止の確認・回復』で記録を確認してください」）を返して拒否する一方、「取得停止の確認・回復」（`preview_uncertain_get_recovery` → `_recovery_plan_locked`）を開くと [managed_network_stop_shared_scope.py:309](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L309) の `if state.blocked: raise ManagedNetworkStopped(401)` で拒否され、再起動してもどちらの画面からも永久に復帰できなくなる。
- **直し方**:
  - [managed_network_stop_auth_recovery.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L97-L112) の `confirm()` で、[managed_network_stop_shared_scope.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L178-L187) と同様に `PixivReadError`（`ManagedNetworkGateStorageError` および `gate_cleanup_errors` / `cleanup_failed` を伴うものを除く）を捕捉した際も `_run_gate_cleanup(lambda: shared._finish_inflight(intent), 'shared GET completion not durable', primary=error)` で自分が作成した `intent` を確実に掃除してから再送出する。

---

### 2. [High] 共有ゲートが一度 401 復帰（`revision >= 2`）すると以後の局所 401 が共有へ転記されず、停止中に共有専用経路の通信が先行できる

- **ファイルと行**:
  - [managed_network_stop_shared_transport.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_transport.py#L97-L111)
  - [managed_network_stop_shared_transport.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_transport.py#L124-L142)
  - [managed_network_stop_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_startup.py#L173-L175)
- **再現条件**:
  1. 1 回目の 401 復帰が成功すると、共有ゲートの開状態は `revision = 2`（以降 `4, 6, ...`）となり、二度と `revision == 0` には戻らない。
  2. `_mirror_local_stop(shared_gate, status)` は `status == 401` のとき `if state.revision != 0: return False` で共有ゲートへの転記を一律に拒否している。
  3. そのため、一度でも 401 復帰が行われた環境では、① 局所 401 が残っている別プロファイルで起動した場合、または ② 同一プロファイルで再度 401 が発生し共有書込み失敗フォールバック（`_trip_local_after_shared_failure`）等で局所ゲートのみが `revision = 3 (401)` になった場合に、`open_or_initialize_windows_user_stop`・`create_shared_profile_reader`・`_transfer_known_stop` のいずれも共有ゲートを 401 に閉じなくなる。
  4. アプリ内の公開 CDN 画像取得（`services._public_images`：[managed_network_stop_public_cdn.py:45](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_public_cdn.py#L45)）および借用 Vault / Pixiv App API / OAuth 通信（`services.external_vault`：[external_vault_transport.py:295](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/external_vault_transport.py#L295)、起動時 `start_keeper()` 含む）は共有ゲートのみを検査するため、プロファイル自身が局所 401 停止中であるにもかかわらず、本人確認より前にこれらの通信や別プロセス CLI 通信が通過する（不変条件「確認以外の通信が停止中に先行しない」に違反）。
- **直し方**:
  - 同一プロファイルで共有復帰より後に発生した局所 401（例: `local_gate.revision > shared_gate.revision`）や、現在起動・使用しようとしているプロファイル自身の未解除の局所 401 に対して、そのプロファイルのプロセス内で共有専用経路（`_public_images`, `external_vault`）が通信できないようにするか、あるいは `_public_images` / `external_vault` にも `managed_network_stop_scope`（局所ゲート）の停止検査を課して局所 401 中に確認以外の通信が先行しないようにする。

---

### 3. [Medium] 解除コミット区間でのキャンセル競合により、Cookie 保存・局所解除後に `checkpoint()` で共有解除が中断される

- **ファイルと行**:
  - [managed_network_stop_auth_recovery.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L122-L132)
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L363-L380)
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L647-L668)
- **再現条件**:
  1. `recover_draft_auth_401` が 2 GET の本人確認を終え、`with save(uid, ...):` に入る。
  2. `save()` の `yield` より前（[native_startup.py:654-660](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L654-L660)）で `self.store.update(cookie_file=...)`（DB 保存）と `coordinator.install_verified_recovery(...)`（稼働中リーダーの Cookie・世代書き換え）が実行され、続いて `yield` 後の [managed_network_stop_auth_recovery.py:128-131](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L128-L131) で `local._advance_401_locked(None)` → `commit_started.set()` → **`checkpoint()`（130 行目）** → `shared._advance_401_locked(None)` が実行される。
  3. `self.store.update` 開始から 130 行目の `checkpoint()` までの間にタスクキャンセル（設定画面の 10 秒タイムアウト、「取消」押下、ダイアログ閉鎖による `_cancel_auth_task()` 等）が起きると、`stopped.set()` が立つため、**`commit_started.set()` の直後にある 130 行目の `checkpoint()`（または 123 行目の `checkpoint()`）が `ClientClosed` を送出して `shared._advance_401_locked(None)` と `forget_recovered_401` をスキップする**。
  4. 結果として、キャンセルされたにもかかわらず `profile.sqlite3` の `cookie_file` と稼働中リーダーのメモリ状態（`local_generation = 1, shared_generation = 1`）および `local.gate`（`revision = 2, blocked = 0`）だけが書き換わり、`shared.gate` は `revision = 1, blocked = 1 (401)` のまま残る半端な状態になる。
- **直し方**:
  - `self.store.update` を始める直前に最終 `checkpoint()` と所有者・`inflight` 検査を済ませてから `commit_started.set()` を立て、`commit_started.set()` 以降（`store.update` → `install_verified_recovery` → `local._advance_401_locked(None)` → `shared._advance_401_locked(None)` → `forget_recovered_401`）では `stopped.is_set()` による `checkpoint()` 中断を行わないようにする（特に [managed_network_stop_auth_recovery.py:130](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_auth_recovery.py#L130) の `checkpoint()` を削除する）。
  - また、`local._advance_401_locked(None)` や `shared._advance_401_locked(None)` がストレージ例外で失敗した場合に、まだ開いていない世代番号で `coordinator.install_verified_recovery` が稼働中リーダーを書き換えてしまわないよう、`install_verified_recovery` は両ゲートの DB 更新が成功した後（またはロールバック可能な形）で反映する。

---

### 4. [Medium] リーダー生成時の世代番号が DB ではなくメモリ上の `_observed_revision` から採番され、別プロセス等で復帰後に新規作成したリーダーが `StaleCredentialReader` になる

- **ファイルと行**:
  - [managed_network_stop_profile_scope.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_profile_scope.py#L230)
  - [managed_network_stop_shared_transport.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_transport.py#L204-L205)
  - [managed_network_stop_shared_transport.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/managed_network_stop_shared_transport.py#L303)
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L176-L182)
- **再現条件**:
  1. `ManagedUserNetworkStopScope.create_reader` や `create_shared_draft_auth_reader` は `gate.snapshot().revision // 2`（DB 読取）で世代を取得しているが、`ManagedProfileNetworkStopScope.create_reader`（230 行目）、`create_shared_profile_reader`（303 行目）、`SharedProfileStopTransport.__init__`（204 行目）はメモリキャッシュ `gate._observed_revision // 2` を参照している。
  2. `create_shared_profile_reader` では 319 行目の `previous.gate._require_open_locked()` で局所 DB を読むより前に 304 行目の `scope.create_reader` が古い `_observed_revision` を読み、`shared_gate` に至っては生成時に一度も DB を読まない。
  3. そのため、別プロセスや別ゲートインスタンスで 401 復帰が行われて DB 上の `revision` が `2` に進んだ後、既存スコープから `create_shared_profile_reader`・`AccountFollowup._open_reader`・`_confirm_request_notice_reader`（通知バナーの確認）等で新規リーダーを作ると、新規リーダーに古い世代 `0` が刻印され、最初の GET または `_confirm_gate_admission()` 実行時に `StaleCredentialReader` で拒否される。
- **直し方**:
  - `ManagedProfileNetworkStopScope.create_reader` および `create_shared_profile_reader`（ならびに `SharedProfileStopTransport`）でも、ロック下の DB 読取値（`self.gate.snapshot().revision // 2` および `shared_gate.snapshot().revision // 2`、または `_require_open_locked()` 実行後の最新 `_observed_revision // 2`）から `local_generation` / `shared_generation` を設定する。

---

### 5. [Medium] 起動時ロック競合の回復画面および同一パス Cookie 上書き時の `launch_snapshot` 比較不整合

- **ファイルと行**:
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L160-L163)
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L496-L508)
  - [native_startup.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L698-L702)
  - [ui/stop_recovery_view.py](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/ui/stop_recovery_view.py#L38-L43)
- **再現条件**:
  - **経路 A（起動時 `stop_gate_busy` 回復画面）**: 起動時にロック競合で回復画面に入り、「再確認」で 401 停止を検出して `stop_rechecked` になった後（[native_startup.py:825-835](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L825-L835)）、新しい Cookie ファイルを選んで「選択したCookieの認証を確認」（[ui/stop_recovery_view.py:39](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/ui/stop_recovery_view.py#L39)）を押すと、`_recover_selected_401` 内の `self.store.update(cookie_file=...)` により `store.snapshot().revision` が `+1` される。しかし `self.recheck_startup_stop` が非 `None` のまま残고 `self.launch_snapshot.revision` も更新されないため、共有ゲート解除失敗後の再試行や 2 回目の認証確認・通知確認が [native_startup.py:162, 700](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L700) の `self.store.snapshot().revision != self.launch_snapshot.revision` に引っかかり、`profile_changed` でボタンが無効化されて再試行不能になる。
  - **経路 B（同一パス Cookie 上書き時の `cookie_replaced_since_launch`）**: 起動時と同じファイルパス `saved.txt` の中身を別アカウント等の新しい Cookie に上書きして 401 復帰した場合、`install_verified_recovery` は `False`（`recovered_restart`：要再起動）を返すが、[native_startup.py:502](file:///S:/Work/PV/h2-20261128/src/pixivvault_nicegui/native_startup.py#L502) の `if current.cookie_file == self.launch_snapshot.cookie_file: return False` がパス一致だけで `False` を返すため、`scheduler_cookie_restart_required()` や `RankingView._first_load()` が再起動不要と誤認して旧世代リーダーで通信を試み `StaleCredentialReader` / `CookieSourceRejected` を起こす。
- **直し方**:
  - 経路 A: `_recover_selected_401` の `save()` で `cookie_file` のみの更新により `store.update` がコミットされた際、`self.recheck_startup_stop is not None` の場合も自身の保存による `revision` 増分を `launch_snapshot` 比較で自己拒否しないよう更新（または許容）する。
  - 経路 B: `cookie_replaced_since_launch()` で `current.cookie_file == self.launch_snapshot.cookie_file` であっても、`coordinator` が存在するときは `coordinator._check()`（ファイル内容 proof の一致）と稼働中リーダーの世代が現在のゲート世代と一致しているかを確認し、不一致（`recovered_restart` 状態）なら `True` を返す。
