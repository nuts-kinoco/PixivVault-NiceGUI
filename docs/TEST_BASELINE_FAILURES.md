# 基準版から続いていた全体テストの失敗（18 件・エラー 8 件）の原因と処置

対象: 全体実行（`-p required_network_guard -k 'not dpapi'`）で基準版から続いていた失敗 18 件・エラー 8 件。
結論: 26 件すべてを原因特定のうえ修正した。環境起因として残した失敗はない。

修正後の全体実行: `S:\Projects\PV-DEV-20261117\logs\full_k_*.log`（最終実行 full_k_2.log: 2070 passed, 2 skipped, 3 deselected, 失敗 0・エラー 0）。

## 1 件ごとの原因と処置

| 試験 | 原因 | 分類 | 処置 |
|---|---|---|---|
| test_u11_failmsg.py（エラー 8） | 自動使用の session fixture が環境変数 `U11_SOURCE_ROOT` と `U11_EVIDENCE` を必須としていた | 試験の前提 | 未設定時は src を既定の取り込み元とし、証跡の書き出しは `U11_EVIDENCE` がある時だけにした。取り込み元の検査（src 配下であること）は維持 |
| test_bookmark_view.py::test_selection_more_and_stable_existing_cards | 件数表示が「50 / 120 件表示」から「表示 N 件 / 条件一致 M 件 / 読込済み K 件」の 3 区分へ変わっていた。数値は正しく（50・120・120）、未読込・条件不一致の状態判定とも整合する現行仕様 | 試験が古い | 期待文言を現行に更新。同じ旧書式を期待していた `tools/check_native_window.py`（2 か所）と `tools/check_readonly_library_browser.py` も合わせた。製品コードは変更なし |
| test_external_vault_ui.py 2 件（remote_poison） | 直前の `test_routes_snapshot_preferences` が Client の外で `PreferencesView` を作り、NiceGUI が `core.script_mode=True` と擬似 Client を立てたまま残した。以後の `ui.timer` は preflight 扱いで登録されず、監視タイマーが動かない | 試験の後始末 | 専用 Client 内で生成し、`client.delete()` で後始末。製品コードは変更なし。`ExternalVaultView` の timer 自体は正常（単独実行・修正後の順序実行で通る） |
| test_fixloop2_tray_dependency.py 1 | 試験の要求「標準の Windows インストールにトレイ基盤を含める」に対し、`pystray` が `native` extra にしかなかった | 製品（依存宣言） | `pyproject.toml` の基本依存へ `pystray==0.19.5; sys_platform == 'win32'` を追加（`native` extra も維持） |
| test_premium_f2_session_keys.py::test_existing_off_state_removes_key_left_by_previous_version | 起動時の `_restore` が OFF 記録を見ても旧版が残した `premium-material.dpapi` を削除しなかった | 製品 | 明示的な `off` 記録があるときだけ `_remove_material()` を呼ぶ。記録が無い場合は従来どおりファイルに触れない。削除に失敗した場合は既存の例外処理で `premium-restore-unconfirmed` になる |
| test_startup_owner_lifecycle_20261116.py::test_new_native_draft_save_restart_borrow_entry | 先行する試験（起動系）が、NiceGUI のグローバル `app.state` に closing 済みの ApplicationServices を残し、この試験の起動が「application services are closed」で拒否された。単独では通る順序依存 | 試験の隔離不足 | `isolated` fixture で、closing 済みの残骸だけを取り除く。製品側の「終了済み services は再利用しない」挙動は変更しない |
| test_vault_tls_loopback.py 9 件 | 全体実行が `-p required_network_guard` で全試験にネットワーク禁止を強制し、実 loopback TCP/TLS が目的のこの試験の接続も遮断された（ガード無しでは 11 通過）。試験自身の `RestrictedSocket` と DNS 差し替えが 127.0.0.1 の試験サーバー以外を禁止している | 試験とガードの衝突 | この module だけ autouse の `no_network` を空の fixture で置き換え。外部接続禁止は試験内部の制限が担う |
| test_z01_vault_integrity.py 3 件 | 製品が `invalid-ciphertext`（「鍵ファイルの復号を確認できません」）と `invalid-generation`（「保管庫の構造を確認できません」）を別文言で表示する現行仕様のところ、試験は旧文言「鍵ファイルの復号に失敗しました」を期待していた | 試験が古い | 原因別の現行文言を期待するように更新（原因ごとの区別も検査する） |

## 残る skip（失敗ではない）

| 試験 | 理由 | 今回の変更と無関係な根拠 |
|---|---|---|
| test_managed_downloads.py:252 | 管理者権限なしではシンボリックリンクを作れない（環境起因） | 変更箇所と無関係で、基準版でも同じ skip |
| test_managed_network_stop_gate.py:263 | 同上（symlink 利用不可） | 同上 |
| test_vault_tls_loopback.py:281 | OS が閉じたソケットのハンドル番号を再利用しなかったため、番号再利用の検証が成立しない（OS の割り当て次第） | 実行ごとに skip したり通ったりする（最終の全体実行 full_k_2 では通過）。基準版ではガード衝突の失敗に隠れていた項目。試験自身が理由付きで skip する設計。影響は「ハンドル再利用時に誤って shutdown しない」点の未検証のみ。変更箇所と無関係 |

## 実行から除外している試験

全体実行は `-k 'not dpapi'` で DPAPI 実機を使う 3 件を除外している（`test_currentuser_dpapi_real_roundtrip_uses_generated_credentials`、`test_dpapi_currentuser_round_trip_and_wrong_binding`、`test_off_fence_survives_failed_dpapi_revocation`）。実機の Windows 資格情報を要するため。
