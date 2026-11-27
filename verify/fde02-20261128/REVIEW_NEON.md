`S:\Work\PV\fde02-20261128` の `git diff 9a132baf..aca3ec65`（`src`・`tests`）に対する読取専用の独立レビュー結果です。

**サマリ: High 2 件 / Medium 4 件**（`managed_network_stop*` ネットワーク取得停止ゲートおよび `completed_maintenance.py` の回収経路自体への破壊的変更は確認されませんでした。一方、途中失敗時の `blocked_sources`・`require_settled()` 連携や復元処理本体に以下の問題があります。）

---

### High

#### 1. 親ディレクトリを跨ぐ `os.rename` 後に `st_ino` 一致を要求しており、exFAT / FAT32 上で復元が必ず失敗し全書込・削除が永久停止する
- **ファイルと行**:
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L214-L215)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L297)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L312-L316)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L341)
- **再現条件**:
  - 外付け HDD/SSD 等の exFAT / FAT32 ボリューム上に旧ライブラリと `quarantine` を置き、単体画像または ZIP を物理隔離から復元する。
  - `ready` は `quarantine/pv-.../<rev>/ready`（または `.pv-restore-<rev>/ready`）にあり、`source` とは必ず別ディレクトリのため、Windows の FAT 系 FS では `os.rename(ready, source)` 後に `st_ino` が変化する。
  - 341 行目でファイル自体は復元（ZIP なら旧 ZIP も `displaced` へ移動済み）されるが、直後の 297 行目 `_check_ready(source, record, legacy=True)` が `[info.st_dev, info.st_ino] != record['ready_identity']` で失敗し、`CURRENT` が更新されないまま `ready` が消滅する。以降の再実行・`recover()` でも永久に 297 行目で失敗し続け、対象ソースは `blocked_sources` に固定され、`require_settled()` により他作品の隔離・復元・ごみ箱削除（`purge`）まで全面停止する。
- **直し方**:
  - `ready_identity`（`st_dev, st_ino`）の照合は移動前の `ready` 検証（323・337 行目）のみに限定し、移動後の `source` 検証（297 行目）は `_legacy_input(source)` + `st_dev` + サイズ + 実バイト SHA-256（`record['after']`）一致で判定する（`legacy_physical_quarantine.py` の `_rollback` と同様）。

#### 2. `_restore_member` が既存 `ZipInfo` の Zip64 拡張フィールド（`0x0001`）を除去せず再利用するため、4GB 超 ZIP の復元でアーカイブが破損する
- **ファイルと行**:
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L45-L64)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L185-L186)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L196)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L202)
- **再現条件**:
  - 4GB 超（または `legacy_physical_quarantine.py` が `force_zip64=True` で再構築した 4000 MiB 超）の ZIP で、2 回以上物理隔離した後に古い方を復元する（`before != intent['after_sha256']`）、あるいは copy-mode 除外併用で `active.zip` を再構築する（`entry['sha256'] != before`）。
  - `current.infolist()` の `entry` と `original.getinfo(member)` の `info` は元の Zip64 extra（`0x0001`：移動前の `header_offset` 等）を保持したまま `updated.open(entry, 'w', ...)` / `updated.writestr(info, data)` に渡される。Python `zipfile` は `zinfo.extra` を消さずに新しい `0x0001` を末尾追記するため `0x0001` が二重化し、読込時の `_decodeExtra()` が先頭の古い `0x0001` を採用して後続メンバーが誤オフセット参照・`BadZipFile` となる。しかも出力 ZIP の読戻し検証がないため、破損した ZIP がそのまま `source` に置かれ `CURRENT` へ公開される。
- **直し方**:
  - 既存 `entry` / `info` を直接書き込みに使わず、新しい `ZipInfo(src.filename, date_time=src.date_time)` を生成し、`compress_type`・`external_attr`・`comment` および `0x0001` チャンクを除去した `extra` のみを複製して渡す（196 行目と 202 行目の `info` 使い回しによる破壊的変更も解消する）。書き込み後に生成 ZIP を開き直して全エントリのヘッダ／整合性を確認する。

---

### Medium

#### 3. 単体ファイル復元時に親ディレクトリ不在を事前確認せず `restore.json` を永続化し、旧ライブラリの全書込・削除経路を永久ブロックする
- **ファイルと行**:
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L152)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L164-L165)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L225-L229)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L341)
- **再現条件**:
  - 作者フォルダ内の画像を物理隔離した後、空になった作者フォルダを外部で削除・移動した状態で、ごみ箱からその画像の復元を実行する。
  - `_source()` → `_legacy_input(..., missing=True)` → `_safe_path(path.parent)` は `FileNotFoundError` を無視するため、親フォルダ不在のまま `_stage()` が `restore.json` を書き込み、`_install()` の `os.rename(ready, source)` が `FileNotFoundError` で失敗する。未完了の `restore.json` が残るため、以後の `recover()` が毎回失敗して `blocked_sources` に固定され、`require_settled()`（[legacy_quarantine.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_quarantine.py#L157-L163)）によって他作品の隔離・復元・ごみ箱削除（`purge`）まですべて拒否され続ける。
- **直し方**:
  - `_stage()` で `restore.json` を作成する前に `if not source.parent.is_dir(): raise ValueError(...)` を検証する。また `_install()` で `not is_zip` かつ `not source.parent.is_dir()` の場合（レースで消えた場合）は未変更キャンセル（`return False`）として `restored.json` を `cancelled` で確定させる。

#### 4. ブックマーク用ハードリンクを持つ旧フォルダ画像の復元で inode が分断され、復元後に原本表示・重複スキャン・再隔離が `MediaAmbiguous` で失敗する
- **ファイルと行**:
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L211-L213)
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L341)
  - （影響先: [local_media_adapter.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/local_media_adapter.py#L526-L536)）
- **再現条件**:
  - 旧アプリの `☆ブックマーク(...)` フォルダにハードリンクが存在する作者フォルダ画像を物理隔離（[legacy_physical_quarantine.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_quarantine.py#L517) が元の inode を `unit/<filename>` へ移動）した後に復元する。
  - `_stage()` が `unit/<filename>` ではなく `selected.image` から `_write(ready, data)` で新規 inode を生成して `source` へ置くため、作者フォルダ側と `☆ブックマーク(...)` 側が異なる inode の 2 ファイルに分断される。`ReadonlyLocalMediaCatalog._source()` は同一 inode 以外の 2 パスを `MediaAmbiguous` と判定するため、復元直後から `original` 取得・外部表示・`inspect_presence`・重複スキャンが失敗する。
- **直し方**:
  - 単体ファイル復元で同一ボリュームかつ `unit / intent['filename']` が存在し `_legacy_input` と `before_sha256` 検証を通る場合、`os.link(donor, ready)` により元の inode を維持したまま `source` へ戻し、ハードリンク不可時のみ `_write` へフォールバックする。

#### 5. `restore()` 途中失敗直後に `blocked_sources` が更新されず、`recover=False` 経路・復旧通知・ごみ箱再表示がブロックを検知しない
- **ファイルと行**:
  - [legacy_physical_restore.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_physical_restore.py#L365-L377)
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L466-L478)
  - （影響先: [legacy_quarantine.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_quarantine.py#L242-L259), [legacy_quarantine.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_quarantine.py#L292-L324), [legacy_quarantine.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/legacy_quarantine.py#L580-L594)）
- **再現条件**:
  - `restore()` が `restore.json` 作成後の `_finish()` で `FileExistsError`（退避後の外部ファイル衝突）や `OSError`（`restore-installed` 後の `CURRENT` 更新失敗）により例外終了する。
  - `restore()` は `self.blocked_sources` / `self.physical.blocked_sources` を更新しないまま例外を投げるため、次に `state(recover=True)` や `recover_legacy()` が呼ばれるまでの間、`recovery_status()`・`recovery_notice()` は空を返し、`LegacyQuarantineCatalog.image_token()`（`recover=False`）や `require_available()` 直接呼出も遮断されない。また `DuplicateView.execute_restore()` の `finally` がごみ箱を開いたまま `refresh_trash()`（`recover=False`）を呼ぶため、失敗した同一項目が再び復元可能として表示され、再実行すると `restore()` 冒頭の `recover()` で 1 回目が確定してリビジョン不一致失敗になり `on_changed` も呼ばれない。
- **直し方**:
  - `restore()` で `_stage()` 完了後に `_finish()` が例外を送出した場合、`except BaseException:` で `record['source']` を `self.blocked_sources` と `self.physical.blocked_sources` に即座に追加する（再送出する）。

#### 6. 復元失敗時に案内される「未完了の操作を復旧・再確認」ボタンが非表示パネル内にあり、未スキャン状態から UI 復旧できない
- **ファイルと行**:
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L43)
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L89)
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L98-L99)
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L231-L233)
  - [duplicate_view.py](file:///S:/Work/PV/fde02-20261128/src/pixivvault_nicegui/ui/duplicate_view.py#L468-L471)
- **再現条件**:
  - 重複確認画面を開いた直後（初期状態 `self.mode == 'undecidable'`、`self.scan_result is None`）にスキャンせずツールバーの「ごみ箱」から旧ライブラリの復元を実行し、途中で失敗させる（または `near` モードで失敗し `self.scan_result = None` になった後に `undecidable` へ切り替える）。
  - 470-471 行目で `legacy_recover_button.set_visibility(True)` にして押下を案内するが、同ボタンは `self.near_panel` の子要素であり、`sync()` が `self.mode == 'undecidable'` の間 `self.near_panel` を非表示にし、`self.scan_result is None` のため `near` タブボタンも無効化するため、ユーザーは復旧ボタンへ到達できない。
- **直し方**:
  - `self.legacy_recover_button` を `self.near_panel` の外（77 行目の `self.recovery_notice` と同じ常時表示領域）へ移動し、モードや `scan_result` に依存せず操作できるようにする。
