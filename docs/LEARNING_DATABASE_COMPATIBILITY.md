# 学習DB互換性

移植元：`/projects/PixivVault/database.py`、SHA-256 `4c54e34ef2cf9a082ccd6a1579d676bdc4a767f1a9558f51abf7cce399ec7e7`。

移植先 `legacy/database.py` は元ファイル153行目以降の定数、正規化関数、`Database`クラス全メソッドを元の順序と本文で保持する。変更箇所は `Database.__init__`（元240〜263行）のみ。先頭の本番固定パス・backupディレクトリ・環境変数fallback・本番backup関数を含めず、明示`db_path`と`sandbox_root`必須に変更した。絶対パスを解決してsandbox配下に限定し、接続前に拒否する。接続後の`_create_table`、学習SQL、正規化、Undo、counter更新は元実装そのまま。元ソースをimportする必要はない。

`LearningRepository`は既存`JudgmentLedger`の`record(Judgment)`と`revert(Judgment, operation_id)`を実装する。結果は`get_discovery_swipe_history`等の旧DB getterが正本。session内のoperation IDは成功時のみ使用済みに登録する。複数接続で古いUndoが後続判定を削除する競合を防ぐため、アダプタ専用の`learning_adapter_revision`表と履歴INSERT/UPDATE/DELETEの3トリガーをDBに追加し、work_idごとに**単調増加する世代**を保持する。これは接続をまたぐ更新、値が一致する更新、ABA（A→B→A）でも変わる。旧DBメソッド115本と原版SQLには手を加えない。

アダプタのschema導入は`LearningRepository.__init__`の初期化時に限定する。SQLiteの`executescript()`は既存トランザクションを暗黙commitするため、`db.lock`取得後に`conn.in_transaction`を検査し、真なら`ValueError`で拒否する。呼出元は未commit処理をrollback/commitしてからRepositoryを生成する。拒否時には呼出元のトランザクションを継続し、未commitの変更を保存・破棄しない。

記録時はアダプタ内で元の`record_discovery_judgment`の判定検証・タグ正規化・旧判定逆再生・新判定集計・history UPSERT・連続skip再計算を同じ順序で呼び出し、`Database._transaction()`の`BEGIN IMMEDIATE`内で書込後の世代を取得する。原メソッド自体が`BEGIN IMMEDIATE`するため、その外で囲むだけではネストエラーになる。原メソッドを改変せず世代取得まで原子的にするには、アダプタ側にこのSQLを限定的に複製する必要がある。Undoも同じ理由で原`undo_discovery_judgment`のcounter逆再生・履歴DELETE・連続skip再計算・空集計削除をアダプタ側で忠実に実行する。

Undo時は`BEGIN IMMEDIATE`取得後に記録時世代と現在世代を照合し、一致した場合だけ削除する。競合拒否時はトランザクションをROLLBACKし、history・集計・stack・使用済みoperation IDを保持する。接続間の単一writer運用は前提としない。原版のUndo意味は**現在の履歴削除**であり、以前の判定の復元ではない。既存履歴に追加した世代表の行が無くても初回更新時に世代が作られ、それ以降すべて追跡する。ただしアダプタ導入前の過去の世代を遡って再構成することはしない。

同じwork_idをA→Bと再判定すると、元SQLはAの集計を引いてBの履歴に**上書き**する。アダプタはBの保存成功後、stackから旧Aを無効化する。Undo B後にUndo Aを呼んでもAは復元されず、`ValueError`で拒否する。別work_idのstack順序は維持する。DB書込失敗時はstack・使用済みoperation IDを変更しない。渡されたmetadataは作成時にdeepcopyしてtags等のネストした可変値もsnapshot化する。

**制約**：operation IDの重複防止はRepositoryインスタンスのsession内だけ。セッションをまたいで同一操作IDが再送された場合の永続冪等性は保証しない。再起動時の個別判定履歴はDBに残り、原実装の再判定/Undoは動作する。新しい冪等性テーブルは作成しない。今回の新UIは従来どおり`MemoryJudgmentLedger`を既定として維持する。

検証は`tests/test_learning_repository.py`の`tmp_path` DBのみ。回帰REDでは、A.record(like)→別接続B.record(superLike)→A.revertが例外を出さずBを消した。修正後は別接続、同値再判定、ABA、直接legacy writer、競合失敗時stack/集計不変、正常Undo・再判定を確認する。原版との判定種類・理由・集計・履歴内容の比較も行う。本番DB、Cookie、実Pixiv APIは一切使わない。専用アダプタは未接続であり、本番利用のDBゲート合格を意味しない。

2026-10-22の独立レビューでは、初期化中の`executescript()`による呼出元transactionの暗黙commitも指摘し、上記の拒否とrollback可能性を回帰試験で確認した。最終版の限定試験は実装担当・別のレビュー担当がそれぞれ **20 passed**。統合担当は原版と移植先の`Database.__init__`以外 **115メソッドのAST一致**を再確認した。原SQLの再判定/Undoの意味を変更せず、拒否時には後続writerの履歴と集計が保持される。証拠はignored `artifacts/native-a5d42aa-20261022-01/legacy-ast.json` と同作業単位の試験記録に保存する。

追加の競合窓試験 `test_undo_revision_check_and_delete_hold_one_write_transaction` では、AのUndoが世代を読んだ直後に別接続Bの`record(superLike)`を同期挿入する。Bの`busy_timeout=0`で`database is locked`を直ちに確認し、AのUndo commit後、Bは**同一operation ID**で成功する。最終履歴とtag/author集計にはBの判定だけが残る。sleepやスレッド競争に依存せず、世代SELECTから履歴DELETEまで`BEGIN IMMEDIATE`が保持されることの局所的証拠とする。

原版本文を保持した`legacy/database.py`には元からの行末空白5箇所（空行3箇所、SQL文字列2箇所）がある。AST/SQLの一致を優先して保持し、このファイルのみ`git diff --check`の行末空白検査の既知例外とする。他の新規ファイルは通常の検査を通す。
