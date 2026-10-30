# P3 native binding・旧完了根拠・作者journal行隔離（未試験）

候補: S:/Projects/PixivVault-NiceGUI-p3-binding-closure-20261027
branch: phase11/p3-binding-closure-20261027
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
固定基線212: S:/Projects/PixivVault-NiceGUI-p3-compat-fixtures-20261027
基線SHA: 06c4fbf344c41ecac86e8da8f7ea1803054f110cf791d7ce337622d86467a6ab

212のwrite-end、全212ファイルhash、branch/HEAD、新候補不存在/writer不在を確認して別候補へコピー。旧artifact/TEMP、CoS/Claudeのprocessは変更しない。Codexは製品/検証器/回帰コード作成と静的読合せのみ。pytest/import/構文/build/GUI/nativeを未実行。親回収の212納品8/8 PASS・169製品/tools同一・process0は基線証拠で、今回の変更後受入には継承しない。

## 確定したnative検証穴

旧demo checkerはv2でjob_id→fingerprint集合を作り、historyに対応する正しい1件の存在だけを検査。正しいbindingを残して同jidに偽fingerprintの別operation行を追加しても、孤立jidを追加しても、後続の画像/pixel/receipt検査でoperations全行を読む処理がなく拒否されない。v1はそのblockを通らない。これは静的に成立する反例で、Codexの実試験再現結果ではない。

job_store_schema._job_bindingsで全行を検査。operationは既存JobService._plainと同じ非空/128文字以内/control文字なしで、UUID・ASCII形式を新要求にしない。全jidを利用可能なlive/history snapshotへ結合し、各fingerprintのJSON内容が同じkind/target/titleと一致することを確認。正しいaliasが1行あっても不正行は救済しない。demoはv1/v2とも適用し、v2の退避履歴・live整合と既存receipt/pixel条件を保持。

managed checkerには元から全行のdurable ID/fingerprint/accepted input照合があり、同じ偽行は拒否される。こちらを新たな製品欠陥とは数えず、共通helperでoperation field検証をそろえ、元の全input/receipt/history照合とsingle-work/attempt1条件を残す。故障注入では偽aliasにも正規inputを置き、入力ファイル欠落だけで拒否が通る弱いテストにしない。

新回帰は両checker×v1/v2で正常bindingを保持し、偽fingerprint/孤立jid/不正JSON/control文字/129文字/空operationを追加。正規の日本語・空白を含む非UUID aliasと別JSON書式を正例にする。v2削除済み履歴の正例/偽alias拒否も含む。DB bytes不変を確認するコードで、checkerは修復・再実行・移行をしない。

**v1の証拠限界**: 既にjob行が退避/削除され、元v1にはjob_historyがない場合、そのjidの本物のtombstoneと偽の孤立bindingは現DBだけで区別できない。未知jidを正規だと推定せず読取り受入を拒否する。v1の正規live binding/operationなし互換fixture、v2の退避履歴は保持。製品JobServiceのv1起動互換・migration条件は変更していない。この証拠を欠くv1退避状態のnative受入は未達として区別し、必要なら正確な以前のsnapshot等の根拠を検証担当/親が回収する。新しい永続ファイル形式や強制移行を採用して解決したとは扱わない。

## 指定された旧identity関数の照合と追加修正

_logical/_refresh_admissions/_read_intent/_accepted_projection/_publication_completed/_itemを通して確認した。既知logical keyは元intent/target digestを再検査し初回pinを保ち、旧intentが読めずmanifestにも根拠がなければ種類の未知新規判定を保留する。既知の独立受付と別kindを止めない。_itemは行単位の固定理由へ隔離し、未知の受付を新規として作り直さない。

追加で成立した2経路を修正した。

- _read_intentが不正targetを拒否しても、旧manifest fallbackで同じtargetをpathに使っていた。fallback前に既存64桁digest形式を確認し、所有root外へのreadさえ起こさない。対象2条件の_raw_read到達0回/操作数0/外側合成file不変を検査するコード。
- v1退避の完了判定はmanifest.source一致だけだった。既存producerの完全なschemaを検査してから完了cacheへ入れる。静止画/旧配置のpath・sha256・bytes 3field、既存direct小説/うごイラのsource_sha256/source_bytesを含む5field、autoZIPのsource/archive/sha256を正規形式として保持。相対path、field型、digest、重複JSON keyを検査する。mutable投影fileのbytesを再hashせず、編集/削除済みでも正規完了metadataを履歴として使う。v2の正確なdone履歴は従来通り優先する。

source-only/不正path/不正SHA/重複sourceのv1拒否、正規metadata＋未編集/編集/削除、実TXT/frames directの5field、autoZIP再出力なし/不正名拒否の回帰コードを追加。新しい投影更新方式や失われた旧identityの復元は実装しない。

## 作者別reconcileの確定経路と修正

従来のreconcileはjson.loads、必須field参照、source proof I/Oがfor-loop内で隔離されず、不正specの1行で後続作者まで例外終了する。_completed_projection_sourcesも同じrawを再参照する。この点は静的に成立する。

消費する必須field（kind/work_id/author_id/output_format）とmedia_keyの一致、重複keyを行ごとに検査し、rawを正規化/上書きしない。保存済みjid/fingerprintがある場合は、そのrawとの結合を作者filterより先に照合する。改変されたauthor hintで元作者を不在扱いしない。既存_bindの区切り文字・raw fingerprintの式は同一。新schemaや列は追加しない。

不正spec/binding/proof I/O/journal writeは行単位に理由を保持し、正常行のreconcile/author出力を継続。journal commit後にだけproof/operation evidenceを返すため、失敗行の未永続結合を完了根拠へ公開しない。失敗行はそのまま保全する。修復されて再検査が通れば同じ受付のerror表示を解除する。

作者を判別できる未確定行はその作者の新規bookmark分類を保留。作者不明/結合改変の行は不明のまま扱い、正常作者の出力は進めても未知の新分類をlightと推測しない。既知の受理済みbookmarkは元の受付を返す。queue/failedの既存statusへ固定理由を接続し、raw spec/path/例外メッセージを表示しない。自己点検を独立監査とは呼ばない。

回帰は実author DBと既存合成servicesを使用し、local owner job/proof境界のみ注入するunit試験。不正必須fieldの局所拒否と復旧、壊れたJSON/重複authorの未知扱い、filter前のbound spec改変拒否、proof I/Oとjournal commit失敗の実発火assert/失敗行不変を確認する。別のintegration回帰では実合成CAS＋SaveExportsで、正常作者出力を進めながらknown/unknownの新bookmark分類を保留することを検査するコード。これを実GUI成功には数えない。

## 診断namespaceの照合（製品変更なし）

managed_ugoiraの_budget/_diagnostic_usage/_diagnostics_writable/_legacy_diagnostic/_record_failureを全体で読合せた。媒体budgetは補助診断をno-followの物理metadataで既存総量へ計上し、全diagnostic JSON/ledgerの意味検査はしない。writerは指定jobのfolderだけを厳密検査するため、別jobの不良recordは他jobへの書込みを止めない。namespace root自体がunsafeならそこへの診断書込みを拒否し理由を表示するが、危険なrootへ迂回しない。

旧入力内の診断はexact schema/6keys、job名/attempt、4096bytes、重複key拒否、operation allowlist、errno/WinError型、同input digest/kindの永続accepted job結合を確認。既知attempt超過を拒否し、v1履歴欠損時は補助recordとしてだけ保持する。_loadのZIP/intent SHA・安全検証は別途必須で、診断をdoneや再実行権限へ使わない。新規診断はinput外へstage/fsyncして公開し、不良recordや失敗理由を削除しない。

この範囲で追加すべき確定欠陥は見つけず、managed_ugoira.pyは212と同一hashで保全。既存総量256世代/1024files/256MiBと削除禁止は維持し、真の枯渇時の未達は残る。過去の自己試験/独立レビュー合格をこの読合せで代用しない。

## CoS再確認と独立再レビュー

final/manifest.jsonで全dirtyファイルを照合。旧212/211/204/203/200/198/196のhash・HEAD・branch・dirty集合不変、元回帰/managed_ugoira/JobService不変は専用証跡。新規S専用TEMP/TMP/basetemp/log、既存venvを使い、旧root/process/portsへ干渉しない。新規parallel taskを作らない。

```powershell
$env:PYTHONPATH='<candidate>/src'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_native_operation_bindings.py tests/test_author_reconcile_isolation.py tests/test_p3_binding_boundaries.py tests/test_native_job_history_compat.py tests/test_native_managed_downloads.py tests/test_native_download_jobs.py tests/test_export_acceptance_compat.py tests/test_author_media.py tests/test_author_media_pins.py tests/test_author_output_connection.py tests/test_durable_export_acceptance.py tests/test_export_completion_history.py tests/test_legacy_diagnostic_compat.py tests/test_diagnostic_failure_isolation.py tests/test_ugoira_diagnostic_recovery.py tests/test_managed_ugoira.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/binding-closure.xml
```

新3ファイルはコード上52予定ケース（native31/author6/boundary15）。pytest収集/実行件数ではない。元212納品8件、元診断13、元3失敗、深い277文字MP4の配置/bytes/hash/再起動、nativeのreceipt/pixel/削除保全、正規operation aliasを保持。必要な既存CPU codec環境での対象MP4は別区分で回収しSKIPを合格にしない。

その後、既存保存GUI/作者GUI各2画面サイズ×初回/再起動を元条件で確認。実失敗リストRetry/attempt/CAS、作者未確定の理由表示と正常作者継続、新しい受付を作らない旧identity保留を確認する。Codexは実行していない。

独立再レビューには final/review-source-ranges.json の全file SHA・全体行範囲・指定関数行範囲/block SHAを使う。両native checkerとhelperは全file、author_mediaのreconcile全体とsource/error/dependency methods、save_exports指定6関数と完了metadata/target fallback、managed_ugoiraのbudget/schema/操作結合を省略しない。full diffはdiff-from-212.patch。

未達: 同一作品の新世代投影更新、欠損旧identityの復元、証拠を欠くv1退避bindingのnative受入、診断総容量、ZIP更新/作者改名/物理隔離の未採用契約、全pytest出口・独立再レビュー・配布native・live GET・exact-head CI・総合UX・P3全閉鎖/P4判断。今回、元利用者データにmigration/修復/削除を実行せず、未採用方式を仕様決定していない。最終path/HEAD/dirty/SHA/write-endと所有processは専用finalへ記録する。
