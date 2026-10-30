# P3-347 独立コード監査：管理root内の明示物理移動（2026-10-30）

**判定: REQUEST_CHANGES（P1 1件）**。対象はworker11のX移動実装5製品と、移動専用・既存copyの合成試験、関連consumerの限定静的読取。監査者worker12はこの移動機能を実装していない。元Flet・readonly legacy DB/画像/ZIP・Cookie・実Pixivデータは開いていない。製品・テスト・GUI・checker・Git・ネットワーク・Claudeは**変更／実行していない**。この文書だけを追加した。以下のreproは**コードに基づく静的再現案であり未実行**。

## 対象ソースの同一性

指定された5つのSHAと現物のread-only Get-FileHashが**すべて一致**。各行番号は下記SHA時点のもの。

| 製品 | SHA256 |
|---|---|
| `src/pixivvault_nicegui/managed_generations.py` | `069a48d7e138e22b9392a4d144ca5bcf4de7138d1d1f250242812f5cd7217025` |
| `src/pixivvault_nicegui/managed_quarantine.py` | `54cfe2df23527f6e2954ba3dbba1af57277e7478aa4028a0300c0180d02d98c1` |
| `src/pixivvault_nicegui/managed_downloads.py` | `855f5fb575eb905f462be2cb99d042aff32c78fe4b69e9bfe6eedd69879dc52c` |
| `src/pixivvault_nicegui/duplicate_inspection.py` | `9e52507e798e2c7116fc7200bfa6e28531673971112abf9da1e1a1ba06f3b79a` |
| `src/pixivvault_nicegui/ui/duplicate_view.py` | `351d5f4bd1c7165f387f09711aab6c3429e4c60d07f004347f8db29f96b70611` |

追加試験 `tests/test_managed_quarantine_move.py` SHA256 `8081c771ee916f192238367ae2212170cb7279237cc4c8ff3f53507931b3c991`、実装者記録 `docs/P3_346_MANAGED_QUARANTINE_MOVE_2026-10-30.md` SHA256 `9154a517e3dfaba3958a640bc3436a72554af632da95e23dcafb74a0a1ff229c` を読取。r6保存XML `artifacts/p3-346-move-impact-r6/result.xml` はtests=129, failures=0, errors=0, skipped=1（**128 PASS / 1 SKIP**）、SHA `cab45a4900db4bfafd35ef13309603f4f600619744d5e34d83e382b66e158e8e`。r8保存XML `artifacts/p3-346-move-impact-r8/result.xml` は26 tests / 0 failures / 0 skips、SHA `2035d0d682ac78af99006354a44b177feb897f0f8f320587ad4ddb1be1a54014`。いずれも**実装者自己検証の保存証拠**であり独立テスト実行の証拠ではない。

## P1: 移動直前のsource差替えで唯一の正常な隔離copyを上書きできる

**対象:** `managed_quarantine.py:234-287`（上記SHA）、特に `269-280` のsource/destination statとbytes/SHA/画像検証、`281-284` の再確認から `os.replace(source_io,destination_io)`、`287` の事後`_verify_copies`。関連 `managed_downloads.py:69-86` の `_safe_path` はパスの事前lstatであり、OS renameに渡す**開かれたファイルハンドル／ディレクトリハンドル／inode固定**を保持しない。

**成立条件:** 正規のowned managed rootに限っても、別のローカル主体／並行プロセスが`_read_bounded(source)`・`_safe_path(source)`の直後、`os.replace`の直前に元sourceの内容または名前を変更できる場合。製品内`work_lock`は他プロセスやフォルダへの外部操作を排他しない。極短い競合窓だが、データの保護を保証する実装としては無視できない。

**再現案（S合成fixture専用、今回未実行）:**

1. `tests/test_managed_quarantine_move.py` の既存 `owned` / `plan_for(source, '123:2')` で、`source`に正常ページ`page-2.png`、`quarantine/operations/<op>/...`に同一SHAのcopyを用意する。
2. `managed_quarantine.os.replace`の該当呼出しだけをラップし、オリジナル`os.replace`に入る**直前**に、既存の元`page-2.png`を書き換える（例：短い不正bytes。copyは触らない）。次に本来の`os.replace(source,destination)`を実行する。これは外部書込みの割込みを決定的に挿入する合成例。
3. `source.execute(plan, operation_id=..., mode='move', confirmed_move=True)`。事前のコピー検証・v4`relocating`除外CAS・stat/SHAは全て正しいが、`os.replace`は**新しい不正source**で検証済みdestinationを上書きする。`_verify_copies`は事後に不一致を検出して例外を出すが、既にsource pathはなく、正常なdestination copyも失われている。
4. `state='relocating'`のまま、復元は`_verify_copies`で拒否される。`saved_receipts()`も正常な欠損proofを得られない。元receiptは変更されないため整合エラーにはなるが、正しいページbytesの保全を回復できない。

同じパス差替え競合窓で、最後の確認後にhardlink/reparseを注入しても、`st_nlink==1`／no-reparseの**実move時点の保証**は成立しない。これらは同じTOCTOU根本原因の変種であり、別件として重複計上しない。現試験の`test_move_rejects_foreign_and_linked_source`（move test:116-137）は**move前から存在するhardlink**を検査するが、直前差替えを注入しない。

**影響:** 元ページが隔離先への移動時に消え、copyも元receiptと一致しない状態になり得る。単なるfail-closed表示だけでは「復元可能」というmoveモードの重要な約束を満たせない。**P1**。ソースの再確認と`os.replace`を同じオブジェクト／安全なhandleの所有契約へ結び、事前に保持した検証済みcopyを破壊せずに差替え・衝突・再parseが起こっても復元可能とする必要がある。修正後は上記差替えの決定的注入（内容・hardlink/reparse・rename前後・restart・restore）を原assertのまま合成CPUでRED→GREEN確認すること。

## その他の境界の限定評価（P2確定事項なし）

| 観点 | 静的な到達・限界 |
|---|---|
| copy既定／明示move | `managed_quarantine.py:338-345` が `mode='copy'`既定・`confirmed_move=True`必須。`ui/duplicate_view.py:68-82,459-485,504-509,522-535` は明示チェック後のみmoveを中継。旧copy tests `test_managed_quarantine.py:139-156,159+`、`test_managed_quarantine_move.py:25-36,213-250`の自己検証と整合。 |
| copy SHA → durable intent → rename | `managed_quarantine.py:194-232,374-426,435-444`：新ownedsourceとcopy・manifestを照合し、v4の除外を先行。移動はsame-device `st_dev`で制限。根本的な未解決は上記P1の最終rename直前。 |
| receiptとmissing | `managed_generations.py:377-526,544-572` はv4の完全一致manifest・source receipt・copy SHA・mode/stateに結びつけ、`managed_downloads.py:242-328,538-567` は不足ページのexact relocation proofのみ許容。非対象missingは `test_managed_quarantine_move.py:253-265` で拒否。元receipt更新・偽造の実装は見つからない。 |
| crash/restart/partial | `managed_quarantine.py:435-444,446-504` は例外後に`relocating`・`restoring`を保持し再起動復元可能な状態モデル。実装者の`test_managed_quarantine_move.py:76-102,139-168,170-184`は途中失敗・final CAS前crash・復元衝突を合成確認。ただしP1の正常copy喪失には対応していない。 |
| CAS/他作品世代 | `managed_generations.py:286-365,609-700` の対象work tokenとoperation revision検証、`test_managed_quarantine_move.py:268-286`の無関係な224の更新。移動対象の保存置換は防止されるように読める。 |
| owner/links | `managed_quarantine.py:60-65,128-148,234-287`・`managed_downloads.py:69-86`はowner marker・no-reparse・1 link・same-volumeを事前検証。`os.replace`がhandle固定ではないため、別プロセス／同一root外部変更との原子的境界は未達（P1）。 |
| 通常DL | `managed_downloads.py:604-642,665-729` は除外を開始時に捕捉し、excluded pageに既存proofを使い、最後のwork-state tokenでCAS。旧成功copyの互換受入より広い実network成功は未検証。 |
| GPU類似 | `managed_similarity.py:455-496,499-535,635-706` のsnapshot列挙は`source_pages`で除外を落とし、各画像の保存時には`assert_page_visible`を挟む。現在のmove済み欠損をembeddingとして誤って復活させる新分岐は静的には見つからない。`verify_pages=False`の検索最適化は現時点のbytes完全検証ではないため、実GPU実行済みという主張はしない。 |
| 旧save_exports/ZIP | `save_exports.py:95-165,502-539,594-615`、`legacy_zip_export.py:138-242`は可視ページとstate tokenを受付時／公開時に照合し、誤受理を防ぐ設計。既存copy tests `test_managed_quarantine_outputs.py:44-73,76-92`・`test_managed_quarantine_consumers.py:137-154`を確認。move固有の公開完了までの実GUI/実ZIP試験は未確認。 |
| ドライブ容量 | 移動先は同一volume隔離なので元保存フォルダのbytesは減るが、隔離ファイル実体は残り、**volume全体のfree bytes増加は保証しない**。`ui/duplicate_view.py:71-77`の文言は適切。 |

**P2:** この静的範囲でP1と独立した具体的再現を確立できたP2は0件。未測定の性能・実GUI・実GPU・実ZIP・multi-process競合などを、証拠なしにPASSともP2とも判定しない。コピー元の保存状態に異常がない場合の一般的な合成経路は好ましいが、上記P1により**X物理moveの限定APPROVEは不可**。修正と同一SHAの再受入後に再監査する。U機能の承認はこの監査の対象外。
