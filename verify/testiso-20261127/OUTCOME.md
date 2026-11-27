# テスト隔離・実 LocalAppData 番兵 実装および検証結果 (`testiso-20261127`)

- 作業ツリー: `S:\Work\PV\testiso-20261127\`（ブランチ `testiso-20261127`、基点 `a5c38a70`）
- 変更範囲: `tests\` と本報告書のみ（`src\` は無変更）
- 制約遵守: 隔離と番兵の導入・合成テスト確認前に既存テストを実行しないこと、実 LocalAppData のファイルを読まない・書かない・一覧しないこと、新規ファイル・一時領域・ログをすべて `S:` 配下に置くことを厳守した。

---

## 1. 実環境の停止記録および LocalAppData に触れていた経路の一覧

`src/pixivvault_nicegui/` 全体に対して `git grep` および AST 全数スキャンを実施し、実環境の停止記録（`<LocalAppData>\PixivVault-NiceGUI\managed-pixiv-get-stop-v1`）または LocalAppData / AppData に到達しうる全経路を洗い出した。

### 1-1. Windows Known Folder API 経由の停止記録経路（`LOCALAPPDATA` 環境変数を無視）
| モジュール・箇所 | 記号・仕組み | 実環境へ抜ける条件 |
|---|---|---|
| `src/pixivvault_nicegui/managed_network_stop_shared_scope.py:392-418` | `_windows_local_app_data()` | `ctypes.windll.shell32.SHGetKnownFolderPath(FOLDERID_LocalAppData)` を直接呼ぶ。環境変数 `LOCALAPPDATA` を変えても実 LocalAppData を返す。 |
| `src/pixivvault_nicegui/managed_network_stop_shared_scope.py:421-424` | `shared_owner_root()` | `test_owner_runtime.owner_root()` が `None` のとき `_windows_local_app_data() / "PixivVault-NiceGUI" / "managed-pixiv-get-stop-v1"` を返す。 |
| `src/pixivvault_nicegui/managed_network_stop_shared_scope.py:427-437` | `open_existing_windows_user_stop()` | `shared_owner_root()` を開き、`ManagedUserNetworkStopScope` 経由で `.pixivvault-user-stop-owner.v1.json` 読込、`.pixivvault-user-stop.sqlite3.lock` の Win32 `CreateFileW` + `LockFileEx`、SQLite 接続を行う。 |
| `src/pixivvault_nicegui/managed_network_stop_startup.py:149-270` | `open_or_initialize_windows_user_stop()` | `shared.open_existing_windows_user_stop()` および `shared.shared_owner_root()` を呼び、不在時は実 LocalAppData 配下へのディレクトリ作成・ロック作成・SQLite 初期化まで行う。 |

### 1-2. 直接 `from ... import` により `shared_scope` への monkeypatch が効かない経路
| モジュール・箇所 | 直接 import された名前 | 影響 |
|---|---|---|
| `src/pixivvault_nicegui/managed_stop_migration_binding.py:7, 12` | `from .managed_network_stop_shared_scope import open_existing_windows_user_stop` | `_association()` が `getattr(scope, 'shared_owner', None) or open_existing_windows_user_stop()` を呼ぶ。テストが `managed_network_stop_shared_scope.open_existing_windows_user_stop` を monkeypatch しても、直接束縛された元の関数が呼ばれ実 LocalAppData に接続・ロックする。さらに L29 の `except Exception: return None` が失敗を握り潰すため、テストが表面上 PASS したまま裏で実環境へ触れていた。 |
| `src/pixivvault_nicegui/managed_stop_migration.py:29` | `from .managed_network_stop_shared_scope import ..., _windows_local_app_data, ...` | モジュール属性として `_windows_local_app_data` が直接束縛されている（現行 `StopMigrationService.target_root` は `shared_owner_root()` 経由だが、直接束縛名が残存）。 |

#### 上記 `managed_stop_migration_binding` 経由で実 LocalAppData に抜けていた主要テスト群
`prepare_native_storage_binding()` / `bind_new_native_storage()` / `require_native_storage_binding()` / `verified_media()` が `scope.shared_owner is None` の状態で呼ばれていた箇所：
- `tests/test_stage5_r1_native_storage_binding.py`（L55, 57, 62, 64, 74, 87, 129-135, および L183 で `userstop.open_existing_windows_user_stop` のみ monkeypatch していた `bootstrap.main` 経路）
- `tests/test_stage5_r1_native_storage_missing_marker_monitor.py`（L40 等）
- `tests/test_stage5_r1_profile_existing_root_refusal.py`（L82, 88）
- `tests/test_stage5_r1_profile_product_wiring.py`（L99, 101 で再生成した `reopened` に `shared_owner` 未設定のままバインド）
- `tests/test_migration_connections.py`（L67, 69, 79, 126, 128 等）

### 1-3. 環境変数 `LOCALAPPDATA` / `APPDATA` 経由の経路
| モジュール・箇所 | 参照内容 |
|---|---|
| `src/pixivvault_nicegui/native_startup.py:20-25` | `native_entry_arguments([])` が `PIXIVVAULT_PROFILE_ROOT` 未設定時に `os.environ.get('LOCALAPPDATA') / 'PixivVault-NiceGUI-Profile'` を返す。 |
| `src/pixivvault_nicegui/browser_cookie_source.py:223-224` | `default_browser_cookie_source()` が `os.environ.get('LOCALAPPDATA')` と `os.environ.get('APPDATA')` をブラウザプロファイル探索に使う（`bootstrap._wire_browser_cookie_source` 経由）。 |
| `src/pixivvault_nicegui/desktop_integration.py:424, 439` | `os.environ` の `LOCALAPPDATA` 配下（`Programs/Honeyview/Honeyview.exe`）を探索する。 |

---

## 2. 入れた隔離と番兵（`tests/conftest.py`）

新規ファイル: `tests/conftest.py`

### 2-1. 実 LocalAppData パスの非接触解決と純粋文字列照合
- 起動時（`conftest.py` 読込時・monkeypatch 前）に 1 回だけ、Windows Known Folder API（`SHGetKnownFolderPath(FOLDERID_LocalAppData)`）および環境変数 `LOCALAPPDATA` から文字列パスを取得し、`REAL_LOCAL_APP_DATA_ROOTS` に正規化保存する。
- 取得・照合のいずれにおいても `stat` / `lstat` / `realpath` / `listdir` / `open` 等のファイルシステム操作は一切行わない（`file:` URI 解析・`\\?\` 接頭辞除去・`os.path.abspath` + `normpath` + `normcase` の純粋文字列正規化のみ）。

### 2-2. autouse 隔離フィクスチャ（`isolate_real_local_app_data`）
各テスト関数ごとに `tmp_path_factory.mktemp("isolated-appdata")` で独立した一時領域（`--basetemp` = `S:\Work\_tmp\PV\testiso\...` 配下）の `Local` / `Roaming` を作成し、各テスト自身の `tmp_path` の中身を汚さずに以下を差し替える：
1. `pixivvault_nicegui.managed_network_stop_shared_scope._windows_local_app_data` → `lambda: isolated_local`
2. `pixivvault_nicegui.managed_stop_migration._windows_local_app_data` → `lambda: shared_scope._windows_local_app_data()`（直接 import 名を動的委譲化）
3. `pixivvault_nicegui.managed_stop_migration_binding.open_existing_windows_user_stop` → `lambda: shared_scope.open_existing_windows_user_stop()`（直接 import 名を動的委譲化。個別テストの `monkeypatch.setattr(shared_scope, "open_existing_windows_user_stop", ...)` も正しく効き、未 patch 時も隔離先 `isolated_local` を参照）
4. `LOCALAPPDATA` → `isolated_local`、`APPDATA` → `isolated_roaming`
   - ただし、未設定時の `PLAYWRIGHT_BROWSERS_PATH` だけは実 LocalAppData のファイルに一切触れずに純粋な文字列結合 `os.path.join(_ORIGINAL_ENV_LOCAL_APP_DATA, "ms-playwright")` で引き継ぎ、既存の Playwright テストが導入済み Chromium バイナリを見つけられるようにした。

### 2-3. 番兵（`sys.addaudithook` + Win32 `CreateFileW` ラッパー）
- `conftest.py` 読込時点でプロセス全体の `sys.addaudithook(_audit_hook)` を登録し、収集段階（import 時）から終了まで実 LocalAppData 配下へのアクセスを監視する。
- 監視イベント: `open`（読み書き問わず全モード・`os.open` 含む）、`sqlite3.connect`（URI 形式含む・`:memory:` 除外）、`os.mkdir`、`os.listdir`、`os.scandir`、`os.remove`、`os.rmdir`、`os.rename`（`os.replace` 含む）、`os.link`、`os.symlink`、`os.truncate`、`os.chmod`、`os.chown`、`os.utime`、`os.chflags`、`tempfile.mkstemp`、`tempfile.mkdtemp`、`glob.glob`、`pathlib.Path.glob`、`pathlib.Path.rglob`、`shutil.*`。
- さらに Python の `open` audit event を経由しない `managed_network_stop_gate._create_file`（Win32 `kernel32.CreateFileW`）を `isolate_real_local_app_data` 内でラップし、ロックファイル等の直接オープンも `win32.CreateFileW` として検知・遮断する。
- 違反時は `BaseException` の直接サブクラス `RealLocalAppDataAccessError` を送出するため、`managed_stop_migration_binding._association` の `except Exception:` や `_gate_storage_io` に握り潰されない。加えて違反を内部記録し、フィクスチャ teardown 時にも未消費違反が残っていれば `pytest.fail` で失敗させる二重防壁とした。

---

## 3. 番兵の確認（合成テスト）

新規ファイル: `tests/test_local_app_data_isolation_sentinel.py`

実 LocalAppData 配下の存在しない合成子パス（`__synthetic_sentinel_probe_never_created__`）を対象に、OS 呼び出し前に番兵が遮断することと隔離の動作を検証した：
1. `test_sentinel_blocks_open_sqlite_mkdir_list_rename_and_createfile`: `open("rb")`, `open("wb")`, `sqlite3.connect(path)`, `sqlite3.connect(uri, uri=True)`, `os.mkdir`, `os.listdir`, `os.scandir`, `os.rename`, `stop_gate._open_existing_stop_lock`（`win32.CreateFileW`）のすべてが `RealLocalAppDataAccessError` で遮断されることを確認。
2. `test_sentinel_is_not_swallowed_by_migration_binding_except_exception`: `managed_stop_migration_binding.verified_storage` の内部 `except Exception:` ブロックを経由しても `RealLocalAppDataAccessError` が握り潰されず例外送出されることを確認。
3. `test_autouse_isolation_redirects_known_folder_direct_imports_and_env`: Known Folder 解決・直接 import 名（動的委譲およびテスト側 monkeypatch 追従）・`LOCALAPPDATA`/`APPDATA` 環境変数の隔離と、`tmp_path` 配下の通常 I/O に偽陽性が出ないことを確認。

実行結果（既存テスト実行前に単独実施）: **3 passed in 0.42s**（終了コード 0）。

---

## 4. テスト実行結果と原証拠

共通実行条件:
- venv: `S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe -B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"`
- `PYTHONPATH`: `S:\Work\PV\testiso-20261127\src;S:\Work\PV\testiso-20261127\tests`
- `TEMP` / `TMP` / `TMPDIR`: `S:\Work\_tmp\PV\testiso\tmp`
- 番兵: すべて有効のまま実行

| 段階 | 対象 | `--basetemp` | 実測結果 | 原証拠ログ |
|---|---|---|---|---|
| 1. 合成テスト | `tests/test_local_app_data_isolation_sentinel.py` | `S:\Work\_tmp\PV\testiso\basetemp` | **3 passed** (0.42s), exit 0 | 標準出力確認済み |
| 2. 停止・移行・起動関連 26 ファイル | `test_stage5_r1_native_storage_binding.py` 等 26 ファイル | `S:\Work\_tmp\PV\testiso\basetemp` | **257 passed** (68.42s), exit 0 | `S:\Work\_tmp\PV\testiso-logs\targeted.log` |
| 3. 全体実行（1 回目・調整前） | `tests/` 全体（5,692 収集 / 5,689 選択） | `S:\Work\_tmp\PV\testiso\basetemp` | 5,672 passed, **4 failed**, 13 skipped, 3 deselected (1874.04s), exit 1（番兵違反 0 件） | `S:\Work\_tmp\PV\testiso-logs\full.log` |
| 4. 調整後ピンポイント再検証 | Playwright 5 ファイル + `test_p6_c_drive.py` + `test_ag_r7_legacy_long_path.py` + 合成テスト | `S:\Work\_tmp\PV\testiso\pytest-basetemp` | **47 passed** (20.89s), exit 0 | `S:\Work\_tmp\PV\testiso-logs\recheck_playwright_and_longpath.log` |
| 5. 最終全体実行（番兵有効） | `tests/` 全体（5,692 収集 / 5,689 選択） | `S:\Work\_tmp\PV\testiso\pytest-basetemp` | **5,686 passed, 3 skipped, 3 deselected, 0 failed** (1867.29s / 31m07s), exit 0（番兵違反 0 件） | `S:\Work\_tmp\PV\testiso-logs\full_final.log` |

※ 最終全体実行の 3 skipped はいずれも Windows 非管理者環境での既存 symlink 権限スキップ（`test_legacy_managed_startup.py:306`, `test_managed_downloads.py:257`, `test_managed_network_stop_gate.py:263`）。

---

## 5. 逸脱日誌（初回全体実行での想定外の発見）

1. **発見**: 初回全体実行で `LOCALAPPDATA` を隔離した際、`PLAYWRIGHT_BROWSERS_PATH` が未設定だったため Playwright テスト 3 件が `Executable doesn't exist` で失敗し、10 件が `Playwright Chromium is unavailable` でスキップされた。
   - **証拠**: `S:\Work\_tmp\PV\testiso-logs\full.log` L563-783
   - **実施した行動**: `tests/conftest.py` で `LOCALAPPDATA` 差し替え前に元の `os.environ.get("LOCALAPPDATA")` 文字列と `"ms-playwright"` を文字列結合するだけで `PLAYWRIGHT_BROWSERS_PATH` を補完（実 LocalAppData の `stat`/`is_dir`/`listdir`/`open` は一切行わない）。再検証 47 件および最終全体実行で 13 件すべて PASS を確認。
   - **残るリスク**: Playwright テスト起動時、外部子プロセス（Node/Chromium）が `PLAYWRIGHT_BROWSERS_PATH` からブラウザバイナリを読み取る（PixivVault 停止記録とは別ディレクトリであり、プロファイル・キャッシュ書き込み先はテスト側の `isolated_local` / `work` に隔離される）。
2. **発見**: 初回全体実行で `--basetemp="S:\Work\_tmp\PV\testiso\basetemp"` を指定したところ、`tests/test_ag_r7_legacy_long_path.py::test_independent_long_root_get_services` が `assert len(str(root)) >= 180` に対して `175 >= 180` で失敗した（`tmp_path / ('l' * 110)` が短い basetemp パスだと 180 文字に 5 文字足りない既存テスト側の前提依存）。
   - **証拠**: `S:\Work\_tmp\PV\testiso-logs\full.log` L543-562
   - **実施した行動**: 依頼対象外の既存テストの閾値や実装を変更せず、同じ `S:\Work\_tmp\PV\testiso\` 配下の `--basetemp="S:\Work\_tmp\PV\testiso\pytest-basetemp"` で実行して `len(str(root)) >= 180` を満たすことを確認（最終全体実行で PASS）。
   - **残るリスク**: 31 文字以下の極端に短い `--basetemp` で全テストを走らせると同テストが再び 180 文字未満になる可能性がある。

---

## 6. 残る経路・制約（`tests/` 変更のみでの限界）

1. **子プロセス（`subprocess.Popen` 等）で直接起動される Python プロセス**:
   - `tests/conftest.py` の `monkeypatch` と `sys.addaudithook` は pytest プロセス内にのみ作用する。子プロセスが `isolated_test_workspace` なしで直接 `open_existing_windows_user_stop()` や `bootstrap.main()` を呼ぶテストが将来追加された場合、子プロセス側には Known Folder 差し替えと番兵が引き継がれない（既存の `test_shared_stop_busy_recovery_20261122.py` の子プロセスは引数で渡した隔離パスのみをロックするため安全）。
2. **`src/` 側の直接 import 自体**:
   - 今回の依頼制約により `src/` は変更していないため、`src/pixivvault_nicegui/managed_stop_migration_binding.py:7`（`open_existing_windows_user_stop`）および `src/pixivvault_nicegui/managed_stop_migration.py:29`（`_windows_local_app_data`）の直接 import 自体は `src/` に残る。`pytest` 経由以外（独立スクリプト等）で直接 import モジュールを monkeypatch する場合は引き続き注意が必要。
3. **audit hook 外の低レベル OS 呼び出し・レジストリ**:
   - `os.stat` / `os.lstat` や、`managed_network_stop_gate._create_file` 以外で独自にロードされる Win32 `ctypes` ファイル API、および `winreg` 経由のレジストリ参照は `sys.addaudithook` の監視対象外。
   - また、Playwright ブラウザテストでは外部の Chromium バイナリ実行ファイル参照に `PLAYWRIGHT_BROWSERS_PATH` を使用する。
4. **独立検証（Sol）について**:
   - 本変更は `tests/` と報告書のみで `src/`（取得停止ゲート本体やデータ保全・ロック実装）は一切変更していないが、テスト実行基盤における取得停止ゲート隔離に関わるため、必要に応じて独立検証（Sol）での確認を推奨する。
