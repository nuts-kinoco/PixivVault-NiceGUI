# P3 durable export acceptance and author dependencies（未検証候補）

候補 S:/Projects/PixivVault-NiceGUI-p3-durable-export-20261027、branch phase11/p3-durable-export-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。固定196 / SHA89c1f349f2b1d113c437e36015f64370e92d65d5703f821549624e1abdc55552を全hash照合して別候補へコピー。旧検証artifacts/データ/processには触らず、manifest対象だけを引継ぎ。旧CPU81PASS/4SKIP・GUI8/8は変更後に継承しない。

## 受付の根本修正（指摘①④）

50件job窓を受付記憶として使わない。JobServiceの既存operationsテーブルを引き続き唯一の永続受付根拠とし、operation_count/accepted_operationsの読取りAPIを追加。既存rowidの受理順をload時にも保持する。schema/DBを追加せず、max_jobs、operation上限、pause/stop/retry/delete/tombstoneは変更しない。

SaveExportsは永続operationsから論理要求→最初の受付descriptorを再構成する。新operation IDはkind:request:<logicalSHA>とし、物理author_folder pinを含まない論理要求をキーにする。JSON target SHAは既存どおりpinを含んだpayloadのhash。旧kind:<targetSHA>も初回移行時にintentを安全読取りして同じindexへ導入する。旧配置/旧job/旧manifestを改変しない。

通常の再受付はindexから対象intent1件を再検証し、物理再分類しない。operationsが増えない間は全job JSON走査を繰返さない。表示窓を退避した既存受付はRetainedExport descriptorとして返す。これはJobSnapshotでもdoneでもなく、完了の主張をしない。既存manifest/source一致を確認し、欠落/破損は保留表示する。新job/operation aliasの追加、退避したjobの自動復活をしない。explicit deleteのidempotency tombstoneも再実行許可にしない。既に存在する過去duplicateを削除せず最初の確定受付を採用する。

既存intentのPermissionError/JSON破損/target SHA不一致を「未発見」として扱わず、再固定/上書きしない。新形式はlogical keyがledgerにあるため該当要求を保留。旧形式が起動時に読めずlogical key自体が不明な場合は、そのkindの初回受付判定を安全に保留する（別kindは独立）。読めるようになれば同じ旧受付を回復する。未確定のorphan JSONだけを受理済みと推測しない。

## 作者依存（指摘②③）

scan_authorsの戻り値は「全体不明(None)」または「未確定作者ID集合」。全体DB障害時は作者集合が不明なのでbookmark新規分類を保留。単体受付/証跡検査が失敗、pending/paused/error等の作者はその作者だけ保留し、他の確定作者は継続。同作者の複数mediaは全件settleが必要。退避済み作者受付も既存manifestと所有出力bytesを確認してから依存を解決する。source proof変更を旧受付へ差替えない。

static段/media段だけでなく、直接の新bookmark enqueueも同じ依存確認を通る。既に確定した同一要求は元受付を返す。DB/受付障害を_itemで単に飲んでlight pinを確定する経路をなくす。autoZIPの独立処理は保留集合と分離する。

## ZIP先・tie・理由表示

新しい配置決定では既存projection author folderがあっても旧master ZIP所在を先に確認。未採用のZIP更新/改名契約を解除せず、その作者は保留する。既に確定した要求の配置を後から再分類しない。

原core.py2430–2431のmax(key=getsize)と同じく、同サイズは列挙順先頭。名前最大のtie-breakを撤去した。安全なpath検査を維持し、複数候補の最大選択/警告を残す。警告の理由・対象をscan_warnings/scan_errorsで保持し、既存queue/failedのrole=statusラベルへ表示。元例外/raw pathは表示せず、固定理由と項目IDを使う。エラー/警告は変化時だけログへ出し、正常復帰で解除する。同じ拒否の0.5秒周期ごとのWARNING連打や新job生成はしない。状態が変わった場合の復帰確認は維持。

## 回帰と検証（Codex未実行）

新tests/test_durable_export_acceptance.pyの11ケース: 実52件受付/50件窓で最初のjob退避後にmaster出現→同受付再利用→再起動（新/旧形式2例）、一時PermissionErrorで再固定しない/復帰（新旧×再起動4例）、作者DB全体障害→bookmark未受付→復帰後正しいheavy、作者単体受付失敗→独立作者bookmark進行→同作者復帰後heavy、旧ZIP+既存projection folderでも拒否、壊れたintentを変更せず復帰、キューで理由表示と同じWARNING抑制。旧tie試験を実装式による期待計算から制御した列挙順へ変更し、明示的な等サイズ順序の逆転回帰も追加。

検証担当はfinal/manifestの全hash/HEAD/branchと旧196を照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新しい深いS TEMP/TMP/basetemp/logを使用。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_durable_export_acceptance.py tests/test_export_admission_stability.py tests/test_author_projection_layout.py tests/test_author_output_connection.py tests/test_save_exports.py tests/test_direct_projection_references.py tests/test_save_exports_gui_fixture.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/durable.xml
```

JobService本体へ読取りAPI/load順序変更があるため既存JobService保存/再起動/操作の回帰も実行。次に前候補で使ったexport/author GUI計8ケースを同じ条件で再実行し、保留理由表示・正常復帰時の解除・通常Retry・projection reader・再起動を確認。元と同じexpected件数/bytes/参照条件を維持し、旧PASSを合算しない。codec等のSKIPを個別記録。所有process終了/port解放/前後hash一致、8089/PID37168/EMA非干渉。

性能: 50JSON/毎enqueueをindex移行+対象1件読取りへ変更したが、時間・p95等は未実測。初回旧ledger移行と作者manifest/所有bytes検証のI/Oコストは残る。新たな数値目標/容量上限は採用しない。測定結果を自己推定で埋めない。

試験/import/構文/GUI/nativeは未実行、独立再レビュー/CoS受入待ち。ZIP更新/改名/物理隔離の保留契約、全原操作分母、独立監査/native/live/exact-head CI/総合UX/P3閉鎖/P4移行は未達。最終SHA/最小差分/固定196保全/書込み終了は専用final。
