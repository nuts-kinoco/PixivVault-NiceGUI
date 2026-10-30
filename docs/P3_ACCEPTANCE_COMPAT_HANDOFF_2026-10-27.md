# P3 受入互換・依存・診断局所化バッチ（未試験）

候補: S:/Projects/PixivVault-NiceGUI-p3-acceptance-compat-20261027
branch: phase11/p3-acceptance-compat-20261027
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
固定基線204: S:/Projects/PixivVault-NiceGUI-p3-retained-fixture-20261027
基線SHA: 5486a95c1a088d3b37523ce97f1fd3a24e7d3d592cb6f441a6dc564257a29ddb

204のwrite-end、全204ファイルhash、HEAD/branchと新候補不存在/writer不在を照合して別候補を作成。203のCoS実画面/全pytestとClaude読取りレビューに書込み/停止なし。旧artifact/TEMPは引き継がない。Codexはコード作成・静的読取りのみ。pytest/import/構文/GUI/build/native/通信は未実行。

## 元結果と検証器

親回収の203全pytestは2823 PASS / 4 SKIP / 2 FAIL。1件は204のartworks fixture補正、もう1件はmanaged native検証器がv1/table2個を固定したためJobService v2/job_historyを拒否したもの。この結果を今回候補のPASSに継承しない。

tools/job_store_schema.py は製品moduleをimportせず、独立した読取り専用契約を定義。application_id、対応version（1/2のみ）、version別の厳密table集合、列名/型/NOT NULL/PK、余分なview/trigger拒否、JobSnapshotの全field集合/型/範囲/重複JSON keyを検査する。check_managed_download_nativeはoperation fingerprint→live job→accepted input→実receiptを対応づけ、v2 terminal historyが同じlive job/accepted IDと一致し欠落がないことを追加検証。元の「新規専用rootの1作品・初回成功attempt1」条件は維持する。任意の既存利用者DBを認証する検証器にはしない。

同型のv1固定はcheck_download_jobs_nativeにも実在したため同helperを共用。demoの残存job数/全物理receipt/pixel検証は維持し、v2履歴のoperation binding・demo対象・receipt対応も検査する。package/単独scriptの既存import切替に従う。新helperは両native検証器と一緒に検証へ渡す。version照合無効化やversion数値の単純置換ではない。

## 指摘①: 作者依存と世代/編集衝突

確定経路は、以前の作者出力doneがあるのに新source proofのauthor-projectionが別要求となり、同じmutable投影先への再出力がbytes changed/collisionで失敗し、同作者の別作品bookmarkまでpendingに残ること。新回帰は実ManagedPixivDownloadsで合成CASを新世代へ更新し、未編集/ユーザー編集の2条件を作るコード。

作者の既往doneは「master配置が成立した履歴」に使い、現在世代の完了とは別扱いにした。同じ作者の新世代に保留があっても、その履歴があれば無関係な別作品bookmarkは進める。初回masterが一度も成立していない作者は、従来通り未完依存として保留する。未知の作者集合となるDB全体障害も安全に保留する。

同一作品・同形式/配置の過去出力に対して新proofを持つ再受付、または旧author出力と新bookmarkが衝突する場合、書換え不能なjobを増やさず受付前に作品単位の明示保留理由を出す。元copy・編集bytes・CAS原本・旧doneは変更しない。新世代をdoneにしたり旧receiptで新proofを満たしたりしない。

**未解消**: 同一作品の保存済み投影へ新世代を実反映する更新そのもの。編集保全、hardlink派生同士の対応、旧manifest/履歴との整合が必要で、今回の依頼だけで投影の置換/世代別新配置などを採用していない。初回未完で既に部分出力がある場合の世代更新も、既存衝突保全を維持する。元仕様の更新ゴールを別製品仕様へ置換した合格とは扱わない。

## 指摘②: 反復全作者照合

同期scanで1回検証した依存を、そのscan内のstatic/media bookmark受付へ渡す。proofは同じSaveExports・同じthread・現在の同期scopeだけ有効とし、終了済みproofや任意のsetで直接受付を迂回できない。直接enqueueは対象作者だけを照合するAuthorMediaService APIを使い、無関係作者のCAS/source検証を繰り返さない。元reconcileのdefault/scheduler経路は変更しない。

source pin/receipt/worker CAS検査は残る。metadata行の走査と現在対象の検証は残り、性能は未実測。速さや数値目標は主張しない。回帰はbatch全体1回、直接受付は対象作者のみ、実作者DBで無関係proofを呼ばないこと、raw/expired proof拒否を検査するコード。

## 指摘③: 退避候補・static窓・旧未読intent

退避候補はメモリー表示用のまま、再起動時に永続operations/intent/terminal historyから再構築する。scanやstatic50件窓の再発見を待たず失敗リストへ出せる。既存の明示restore→CAS Retry→同job/attempt2、operations不増を変更しない。既存50超回帰へ「再scan前に提示済み」を追加。親回収のEdge成功は条件が整いscanで再発見された限定成功であり、この再起動直後の欠落を否定するものではない。

static完了は表示窓だけでなく永続job履歴とaccepted requestから確認し、現在の実receiptと同じresultを持つdoneだけを使う。保存されたRemoteBookmark show/hideの受付は再起動後の空remote catalogでもbookmark根拠となる。通常作品/ManagedAuthorIntentをbookmarkと誤認しない。必要な現在世代だけを読み、受理済み不変metadataをcacheし、全退避JSONを周期ごとに読み直さない。未完staticがある場合は従来のautoZIP batch待ちを保つが、独立した完了bookmark/他媒体まで止めない。51 fillerにより対象を50窓から外し、空catalog・再起動・同IDの検査を追加。

旧intentが読めずmanifestも無くlogical identityの根拠が完全に失われた場合、digestから旧pinを逆算できない。この場合に未知の新要求を新規だと断定する変更は行わない。既にlogical keyが確定している無関係受付と別kindは継続する。回帰はmanifestのない旧error/壊れたintentと、既知の独立done受付を共存させて保全を検査する。**この未確定identityそのものの復元は未解消**で、元intent等の正確な証拠回復が必要。種類全体の未知新規判定を無条件解放していない。

## 指摘④⑤: 旧診断互換・局所故障の理由

v1で既に窓外だったjobはattempt履歴がない。旧入力内の診断は、安全な単一link bounded file、正確なjob名/attempt/schema/operation/type、重複key/4096bytes、同じinput digestへ結び付く不変accepted operationをすべて確認すれば、補助記録として保全して読める。known attemptがある場合の超過拒否は維持。履歴不明時に診断のattemptをJobSnapshotへ移したりdone/再実行許可に使わない。ZIP/intent hashと安全検証も独立に必須。

旧テストのmissing_history一律拒否ケースを補正し、known上限拒否を残した。代わりに実fixture DBを正規v1構造へ戻し、既にretireしたattempt1/2の本物の診断を残して再起動する正例を追加。未知jobを装った診断は同じrootでも拒否する。

診断writerは対象jobのfolderだけを厳密検査。1件の不良/不明ledgerが別jobの補助診断保存まで止めない。未知fileを許可/削除する変更ではない。初期点検は既存256世代上限で打ち切り、job単位の固定理由を保持、変化時だけ警告、既存queue/failedのstatus表示へ接続する。原例外/raw path/秘密値を出さない。容量/I/O/検査を区別し、元runner例外は置き換えない。故障記録は保存し、再起動時に理由を再検出する。新回帰は旧不良診断を保存したまま、別jobの診断保存と実QueueViewの理由表示まで確認するコード。

総上限256世代/1024files/256MiBと診断削除禁止は保持。**既存診断が本当に容量枠を満たした場合の停止は未解消**。新しい配分値、上限引上げ、削除/退避方式を採用していない。

## 指摘⑥: 異常作者後の継続

sourceの不正作者項目を行単位で記録し、有効aidの不正workはその作者だけを未確定として保留する。任意の不正文字列をログの項目名に流さない。reparse作者の後に正常作者と実保存済みTXT/framesが続く合成fixtureを追加。安全path拒否、元pin、後続bytes/manifest/参照検査は既存回帰でも保持する。

## v1→v2の6所有DB境界

今回JobService自体のschema/migrationコードは204と同一。導入済みv2の影響先は次の各専用root/jobs.sqlite3。

- demo download sandbox（services.py download_writer）
- managed static originals（services.py static_jobs）
- managed novel jobs（managed_novel_jobs.py）
- managed ugoira（managed_ugoira.py）
- save exports（save_exports.py）
- extension application（extension_application.py）

owned marker/DB identity/専有lockとv1→v2 transactional migration、operation上限/50表示窓を維持。v2→v1旧バイナリー互換はない。元利用者rootへmigrationを実行していない。native checkerの新規1作品検証は6種すべての移行受入を証明せず、各所有者のfixture/restart回帰と独立レビューが必要。

## CoSへ渡す未実行検証

最初にfinal/manifest.json、固定204/203/200/198/196の保全、HEAD/branch/dirty、変更一覧を照合。新規S専用TEMP/TMP/basetemp/log、既存venvを使う。旧CoS/Claudeのroot/processへ非干渉。新しいrootの書込み/所有cleanupだけを担当側の許可範囲で実施する。

```powershell
$env:PYTHONPATH='<candidate>/src'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_native_job_history_compat.py tests/test_native_managed_downloads.py tests/test_native_download_jobs.py tests/test_export_acceptance_compat.py tests/test_legacy_diagnostic_compat.py tests/test_durable_export_acceptance.py tests/test_export_completion_history.py tests/test_diagnostic_failure_isolation.py tests/test_ugoira_diagnostic_recovery.py tests/test_managed_ugoira.py tests/test_author_output_connection.py tests/test_author_projection_layout.py tests/test_export_admission_stability.py tests/test_save_exports.py tests/test_bookmark_media_projection.py tests/test_bookmark_media_deep_path.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/acceptance-compat.xml
```

新規3テストファイルはコード上25予定ケース（native14/export8/legacy3）。pytest収集/実行結果ではない。既存のmissing_history一律拒否1条件を正規旧互換へ置き換え、future known attempt拒否は維持。durable回帰の障害注入wrapperへ新kwarg転送だけを補い、判定は同じ。204の実QueueView fixture、元診断13、元3失敗、277文字MP4の後続配置/bytes/hash/再起動を保持。既存CPU codec環境によるMP44件は必要なら別実行として分け、SKIPをPASSへ合算しない。

次に既存export/author Edgeの各4ケース、50窓外→DB再起動→実失敗リスト→通常ボタン→同job attempt2/operations不増を再確認。初回/再起動・2画面サイズ、projection TXT/相対画像reader、autoZIP拒否表示とRetryの元条件を下げない。新世代保留の表示と無関係作品保存、診断故障の理由と別job継続も確認する。CodexはGUIを起動していない。schema helper追加のため、配布native/buildは独立した未達として検証担当へ渡す。

固定203の2823PASS/4SKIP/2FAIL、退避Edge成功、Claude不通過レビューは基線証拠として保存。今回候補の自己試験/独立監査/総合受入に流用しない。全pytest出口、独立再レビュー、配布native、live GET、exact-head CI、総合UX、全原操作分母、P3閉鎖/P4移行は未達。今回の残判断は同一作品投影更新、証拠欠損旧identityの復元、診断総容量。最終差分/hash/process/write-endは専用artifacts finalに記録する。
