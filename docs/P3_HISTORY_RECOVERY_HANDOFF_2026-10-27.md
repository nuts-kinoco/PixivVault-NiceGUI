# P3 完了履歴・明示復帰・補助診断の分離（コード候補、未試験）

候補: S:/Projects/PixivVault-NiceGUI-p3-history-recovery-20261027
branch: phase11/p3-history-recovery-20261027
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
固定基線200: S:/Projects/PixivVault-NiceGUI-p3-ugoira-diagnostics-20261027
基線集合SHA: 022c54c9717d44d50dc003de1cff1213be9cfbc84604b0cbb975b5d7fe47747e

開始時に基線200の全200ファイル、HEAD、branch、writer不在を照合し、新しい別候補へ製品差分だけを複製した。旧artifact/TEMP/検証プロセスは移していない。今回の最後の集約SHA・旧候補照合・最小差分は artifacts/p3-history-recovery-20261027/final/ を参照。Codexは製品/回帰コードの作成と静的読取りだけを実施。pytest、import、構文検査、build、GUI/native、通信を実行していない。

## 原指摘への対応

1. **編集可能projectionと完了履歴を分離**: 作者依存はJobServiceに永続保存されたdoneと同じtarget/resultを根拠に解消する。投影先を毎scanで再hashしない。ユーザーの編集/削除を戻さず、独立した同作者の後続bookmarkを進める。実出力時のsource/CAS検査、衝突時の既存bytes保全は残る。編集済み同一作品への再出力を勝手に許可する変更ではない。
2. **表示窓外の失敗/停止の明示復帰**: 専用JobService DBにjob_historyを追加。既存jobs/operationsと同じトランザクションでterminal metadataを保存。履歴は既存operationに結び付いたjobだけで、既存10000 operation上限以内、表示窓50件は維持。v1専用DBは所有検査/専有lockの後、BEGIN IMMEDIATE内でtable追加とuser_version=2を一括移行する。未所有DBの拒否は維持。履歴のID、fingerprint、型、terminal state、bounded fieldsを検査し、不正履歴を拒否する。
3. **自動再実行はしない**: scanは退避受付を提示するだけ。既存失敗リストへ「退避履歴を再試行」を接続し、epoch検査・off-loop受付・CAS付きRetryを使う。新履歴がある場合は同じjob_id/target/operationを復帰しattempt+1。復帰とRetryの途中で中断してもerror行として既存Retryから再開できる。表示窓の他の未完jobは追い出さない。
4. **v1で既に退避済みだった場合**: 失われたattempt/stateを創作しない。正確な旧intentと対応manifestがあれば初回にpublication metadataを検証して完了根拠をcache（mutable payload hashなし）。manifestも無くattempt不明なら明示操作に限り export-recover:<旧job_id> のリンク付き別operationを作る。旧tombstone/初回pinは残す。再起動時もrecovery chainを台帳から引く。同一job attemptを引き継げたとの報告はしない。DB v2→v1の旧バイナリー互換はないため、実利用データへの移行/配布は今回行っていない。
5. **lockと反復I/O**: 新bookmark受付の作者依存検査を受付RLockの外に移し、再入後に同一受付を再確認。done履歴はメモリー参照で、毎回全projectionのmanifest/payload SHAを計算しない。初回台帳index、要求されたintent1件、元CAS/source検査は必要な場所で残る。DB履歴metadataの保存コストや速度は未実測で、性能合格を主張しない。
6. **旧intent読取不能の局所化**: 旧publication sourceとtarget digestから該当logical identityを独立に確定できる場合は、その要求だけを拒否し他の受付を続ける。既にindexで確定した無関係受付も止めない。対応sourceがどこにも残っておらずidentityが不明な旧受付は、新規扱い/再固定せずそのkindの未知の新受付だけを保留。未知identityと独立だと証明できない新要求を推測で通さない。元intentを上書きせず読取り復旧後は元受付へ戻る。
7. **通常fileの扱い**: author_folderは原版isdirと同様、拡張子なしの通常file …(23) を作者folder候補から除外する。symlink/reparseを候補から黙って落とすことはせず拒否。複数folder最大選択/列挙順tie/警告と、projectionfolderがあっても旧ZIP先の保留は保持。
8. **追加CoS確定欠陥**: logical identityをpacket→JSONの正規内容へ変換してから比較。新要求proofsのtupleと保存JSONのlistは同一の内容となる。pin除外と旧folder設定互換を保ち、未知intent拒否を緩めない。既存test_save_exportsの5jobs/全done/再起動条件に加え、autoZIP再scan/再起動でID・operation数が増えない回帰を追加。

## 補助診断: 修正した範囲と保留

通常媒体の_budgetは補助診断のJSON/ledger/nlink整合を検査しない。診断の物理metadataだけをno-followで既存総量に計上し、未知file/link/reparseを媒体入力や成果物として採用しない。診断writer側は従来通り安全なdirectory/file、単一link、job名とfolder名、operation台帳、厳格JSON schema/重複key/4096 bytes/errno・WinError型/attemptを検査する。不正診断があれば追加診断を書かず保存し、元のrunner例外を置き換えない。正常な空diagnostics namespaceは初期化時に維持する。受理済み入力内の旧診断例外はさらにretained attemptに照合し、退避後の未来attempt/記録不明を採用しない。

**未解消の容量判断**: MAX_CONTROL_GENERATIONS=256 / FILES=1024 / BYTES=256MiB の既存combined上限、診断の保全、削除禁止を同時に維持するため、既に保持済みの診断が真に枠を満たせば新媒体受付/保存も拒否され得る。この蓄積問題を解消したとは扱わない。今回、上限引上げ・診断の削除/移動・別無制限quotaは採用していない。opaque accountingでも読取り権限障害で使用量が分からない場合は安全に容量確認を省略できない。

判断候補は、(A) 同じ総上限の中で診断の保持枠と媒体の予約枠を明示的に配分し、新規補助診断は予約枠を侵食する前に打ち切る、または (B) 既存診断をどの条件で退避/削除できるか保持契約を承認する。推奨はAを設計レビューで決め、既存満杯rootについてはB相当の扱いを別途定めること。具体枠・保持期間・既存rootの扱いは原仕様で未採用なので決めていない。今回のコードは内容不整合の伝播だけを除去した独立候補。容量境界回帰は残る拒否を明示し、P3閉鎖の成功に数えない。

## 最小差分

製品5ファイル: job_service.py / save_exports.py / bookmark_projection.py / managed_ugoira.py / ui/job_views.py。
既存回帰2ファイル: test_export_admission_stability.py の通常file受入修正、test_ugoira_diagnostic_recovery.py の未知aux JSONをwriterで拒否する検査へ修正。元の13診断ケースを削除しない。
新回帰2ファイル: test_export_completion_history.py（parameter展開12ケース）、test_diagnostic_failure_isolation.py（18ケース）。計30はコード上の予定件数で実行/収集結果ではない。
追加文書: 本handoff、PROGRESS追記。基線200以外の機能を作り直さない。

新回帰はedit/delete後の再起動と後続bookmark、50超窓外error/stoppedの再起動→明示Retry→同jid/attempt2、v1退避のリンク付き回復と再起動、v1移行中断の発火assert/transaction rollback、tuple/list再受付、旧intent読取不能と無関係処理、metadata lockの非占有、stale UI event、本物QueueViewのbutton生成/通常handler、通常file/reparse拒否を含む。補助診断はmalformed/unbound/hardlink/symlink/reparse/他job名、重複key、4097bytes、attempt差/超過、errno・winerror text/bool、退避後/履歴不明のattempt、既存上限とRetry蓄積を対象とする。symlink権限不足は従来通り理由を付けてSKIPしOS設定を変更しない。

## CoS限定再確認（Codexは未実行）

final/manifest.jsonの全ファイル、HEAD/branch/dirtyと基線200/198/196の保全記録を先に照合。新しい候補専用の深いS TEMP/TMP/basetemp/logだけを使用し、既存venvで実施。旧rootや実データを使わない。277文字条件を短いrootへ置換しない。

```powershell
$env:PYTHONPATH = '<candidate>/src'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
# TEMP/TMP、basetemp、logはCoSの新S専用領域へ設定してから実行
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_completion_history.py tests/test_diagnostic_failure_isolation.py tests/test_durable_export_acceptance.py tests/test_export_admission_stability.py tests/test_save_exports.py tests/test_ugoira_diagnostic_recovery.py tests/test_managed_ugoira.py tests/test_bookmark_media_projection.py tests/test_bookmark_media_deep_path.py tests/test_job_service.py tests/test_job_views.py tests/test_native_download_jobs.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/history-focused.xml
```

既存CPU codec interpreterが必要なMP4は、その既存環境で対象4ケースを別記録。旧13診断回帰、元3失敗（converter_failure_is_error_not_done_and_retry_can_complete / failed_gif_retry_reuses_exact_completed_frames_without_saving_again / post_proof_frame_failure_retry_reuses_completed_source_and_restores_exact_reference）、277文字MP4後続配置/bytes/hash/再起動の条件を維持。容量境界テストがPASSでも未解消の容量配分が合格した意味にはしない。

その後既存 tools/check_p3_save_exports_browser.py --channel msedge --output <fresh-S-output> の1280/1920初回・再起動4ケース、既存author checkerの4ケースを元条件で実行する。通常ナビゲーション/queue page/失敗リスト、autoZIP done/error/attempt2、TXT/相対画像projection reader、再起動を保持しAPI代行/force clickを使わない。新しい退避経路は専用fixtureで失敗/停止→50超窓外→再起動→失敗リストの同job IDボタン→明示Retry→attempt2/doneを画面で追加確認（前記CPU実render回帰は実ブラウザー証拠の代わりにしない）。ユーザー編集後のCAS不変・編集内容保全も保存先bytesで確認する。

CoSは新候補での結果だけを回収し、旧200のPASSを自動継承/合算しない。前後hash、所有process正常終了/port解放、8089/PID37168/EMA非干渉を記録。独立再レビュー、全pytest出口、配布native、必要live GET、exact-head CI、総合UX、原操作の有限分母、ZIP追記/改名/物理隔離の判断、P3全閉鎖/P4判断は別の未達。自己点検は独立監査ではない。
