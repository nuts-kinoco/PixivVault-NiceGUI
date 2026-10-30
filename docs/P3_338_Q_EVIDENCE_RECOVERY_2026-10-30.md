# Q群の既存証拠回収 — 2026-10-30

担当: worker-7。root: `S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028`。
ファイル名338は依頼された保存名。照合対象には、primeの追加指示で339の既存結果を含めた。338の他者成果は変更していない。
HEAD `2cf4a7a` / dirty502は依頼時のラベルであり、今回はGitで再取得していない。

今回行ったのは、指定台帳・既存対応表・関連source・保存済み結果の読取、限定SHA取得、本書の新規作成だけ。GUI、試験、製品変更、旧DB操作、Cookie、外部API、Gitは実行していない。元152のID・期待・集計・台帳を更新せず、正式PASS追加は0。

## 1. Q03/Q12の前タスクの状態

worker-7自身の修復は取消後未実行。前回の作成物は324対応表のみ。現在はprimeが実装した修復と既存の試験結果が残っており、再編集・再試験の必要はない。

| 対象 | 回収した状態 | 具体変更と根拠 | 今回の判定限界 |
|---|---|---|---|
| Q03 managed作者名・再起動 | 実装済み、保存済みCPU試験完了 | `services.py:1661–1679` の `managed_job_author_names` は `_managed_job_records` の受理済みDTOを読む。`ManagedAuthorIntent.record` を展開し、job種別/作品IDを照合。ディスク読取・GET・enqueue lock待機はない。`ui/job_views.py:423–431` が検索へ接続。 | 関数呼出しによるCPU試験と、ブラウザでの実入力を区別する。managed作者名の現GUI入力証拠は今回未回収。 |
| Q03 RED→GREEN | 325の1 FAILから326の52 PASSへ。339全回帰でも当該node PASS | `tests/test_legacy_failed_managed_retry.py:97–136` は初回の作者名/ID/不一致、再起動後作者名、元/静止コピーSHA不変を検査。既存testを変更していない。 | 339のJUnitから当該nodeを抽出済み。今ターンの再実行ではない。 |
| Q12 一括削除確認 | 文言の実装修復済み | `ui/job_views.py:445–461` で対象ID/revisionを先に固定し、`len(frozen)`件の履歴、取消不可、実行中停止、保存ファイル保持を表示する。 | 326の52 PASSに含まれる旧テストは選択/revisionの保護等。新しい件数/不可逆文言そのものの直接assert、現画面の取消/確定は今回未回収。Q19の確認試験では代替しない。 |
| worker側の新規test | 未作成 | `tests/test_q03_queue_author_search.py` は未存在。`tests/test_job_views.py` は旧16テストのまま。 | ファイル未存在はQ03製品修復が未実装という意味ではない。 |

現在の実装差は `src/pixivvault_nicegui/ui/job_views.py` と `services.py` に確認できる。後者には他担当のretry/削除接続等もあるため、ファイル全体の差をQ03担当変更とみなさない。既存の完了receiptを回収しており、worker-7が所有する未回収の試験プロセスや実行中編集はない。

## 2. 証拠キーと範囲

各パスはroot相対。実装者側の試験結果と独立レビューは別記した。mtimeを実開始時刻として扱わない。

| キー | 既存結果 | 確認した範囲 |
|---|---|---|
| R325 | `artifacts/p3-325-q03-red-r1/result.xml` | 2026-10-30 01:01:49 JST開始、1 FAIL。作者名検索の期待301に対し空集合。 |
| C326 | `artifacts/p3-326-q03-q12-q20-impact-r1/{result.xml,console.log,exit.txt}` | 01:28:59 JST開始、52 PASS、exit0。Q03再起動・原データ不変、job/failed/retryのCPU試験。 |
| C327 | `artifacts/p3-327-row-navigation-green-r1/result.xml` | 02:06:12 JST開始、69 PASS。作品単位/共通障害の区別、ページ/検索/カテゴリ/タブ往復中のbatch継続、明示取消、Q03、Q17等。 |
| R329 | `artifacts/p3-329-failure-actions-r1/{result.xml,console.log}` | 85 PASS / 1 FAIL / 1 SKIP。`test_whole_retry_set_hashes_and_reads_frozen_table_once` がhash2回で失敗。この失敗原票を保持する。 |
| C330 | `artifacts/p3-330-native-and-failure-actions-green-r1/{result.xml,console.log}` | 103 PASS / 1 symlink権限SKIP。339でも上記ライフサイクルnodeのPASSを回収。別の329 dismissal単体r2は47 PASS / 1 SKIP。 |
| E331 | `artifacts/p3-331-failure-removal-edge-r1/result.json` | Q18/Q19/Q20、headless実Edgeのclick/download、1280×720・1920×1080それぞれ初回/再起動の4回PASS。個別取消/17→16、他client古い確認拒否、絞込3件でも全16件確認/取消/0件、再読込・再起動後0件。CSV17/3全列・BOM・式無害化・開始通知。 |
| C339 | `artifacts/p3-339-native/full-r1/run.json` / `result.xml` | 07:58:07→08:05:28 JST、4070 tests = 4064 PASS / 6 SKIP、失敗/エラー0、exit0。前後564入力のJSONは同一SHA。Q03再起動、Q17 53行、batch一回hashの3nodeを既存JUnitから抽出した。 |
| N339 | `artifacts/p3-339-native/jobs-packaged-r1/{first.json,restart.json}` | 両方passed。合成2作品4頁の保存/receipt・再起動、履歴2→1の個別削除でも媒体保持、自動再実行なし。1032×1041/DPR1/DPI96。通常終了exit0、所有子0、app/debug port解放、強制終了なし。managed作者名検索・一括削除・7状態試験ではない。 |

E331の`original_db_unchanged`は生成した専用合成DBの不変であり、本番DBを使ったという意味ではない。`tools/check_failed_records_browser.py:136–211,217–288` の実click/保存/assertと結果を対応付けた。17行CSVは2頁目から出力し、開始通知17件/3件も実assert済み（156/162行）。

N339のJSONにはexe SHAそのものが入っていない。339ディレクトリ内の保存済みnative観測として扱い、起動した配布exeとmanifestの直接結合はprimeのNative/URI回収に委ねる。今回、native成功を無条件に全製品SHAへ継承していない。primeから届いた通常profile-r2の成功報告も、本書ではQの直接実操作証拠へ転用していない。

旧J/F/Mは変更していない `docs/P3_324_Q_ACCEPTANCE_MAPPING_2026-10-29.md:43–87` の別worktree証拠を指す。Jは個別/一括queue、Fは旧失敗一覧/CSV、Mは旧失敗→managed再投入。今回これらを再実行していない。

## 3. 原IDごとの短表

原期待はv8のQ範囲（6558–9027行、19レコード）と324対応表で照合。Q16は独立IDなし、旧Q16の入口はQ07参照に統合されている。Q07の「両入口」というindex記述と、原通常queueにはretryボタンがないという固定コードの差を勝手に解消しない。

SHA列の記号: J=`ui/job_views.py`、S=`services.py`、F=`ui/failed_records_view.py`、M=`failed_records_state.py`、T=`ui/theme.py`、A=`failure_record_actions.py`+`failed_record_dismissals.py`。全て現在の実hashを取得し、331/339の該当入力と一致。Jは324→326で変更し以後同一、S/Fは326→331で後続変更、M/T/JobServiceは324から同一。全体候補が同一という意味ではない。

| 原ID・現原期待 | 実装状態 | 実操作・既存試験証拠 | 現SHA差 | 残る未達・未結合 |
|---|---|---|---|---|
| Q01 状態/進捗、7状態、36px行、共通注意帯/形、N/M・ID、ログ | 基本表示済み、原行契約に差 | 旧Jの状態/頁、N339の完了履歴表示 | J変更、T/JobService不変 | min-height56px・状態別色・背景等のCSS差、原状態アイコン/破線・ID席・専用ログ。CSS値と実測行高は別。7状態/同面積での原見た目は未受入。 |
| Q02 状態チップ×検索AND、変更時先頭頁 | 実装済み | 旧Jのqueued/paused/stopped/all、頁越え選択 | Jは326以後同一 | 残4状態チップの実対象集合、検索AND・頁resetの現実操作。 |
| Q03 作者名/ID部分一致、解除、先頭頁 | managed検索・復旧修復済み | C326/C327/C339の実service再起動CPU。旧J/N339はデモ検索のみ | J/S一致331・339 | managed作者名/ID/解除と再起動後の実入力。CPU試験をマウス/キー操作と呼ばない。再実装不要。 |
| Q04 個別pause、即時受理表示 | 実装済み | 旧Jのrunning→paused/タブ復帰、C326 | J/JobService同一 | 現GUIの受理表示時点を結合。 |
| Q05 個別resume、paused元状態復帰 | 実装済み | 旧Jのrunning復帰、bulk queued復帰、停止済resume無効 | J/JobService同一 | 現GUIの受理表示/元状態対応。 |
| Q06 個別stop、受理→終端、pause解除 | 実装済み | 旧Jのqueued→stopping→stopped | J/JobService同一 | pausedからstopの実操作と、受理/完了表示の区別。 |
| Q07 失敗個別retry、回数/理由/入口 | 部分実装 | 旧J attempt2、旧M別明示クリック3jobs/2receipt、C326/C339 | J/S/Fは現候補に一致 | 原入口参照差、旧失敗novel/ugoira未対応、元行の回数/消込は不変。現GUI再失敗時通知・独立レビューの結合。 |
| Q08 履歴削除、未完了停止、媒体保持 | 実装済み | 旧J個別削除、N339で再起動履歴2→1・4頁/receipt保持 | J/JobService一致339 | 未完了行の取消/確定→停止を現GUIで確認。N339は完了履歴の削除に限定。 |
| Q09 選択集合のbulk stop、件数/解除 | 実装済み | 旧J queued12件・頁越え、stopped13件 | J/JobService同一 | running/paused/queued混在の対象/非対象、受理/拒否/失敗件数の現実操作。 |
| Q10 pausedのみbulk resume、元状態保持 | 実装済み | 旧J paused12→queued、選択解除 | J/JobService同一 | running由来paused混在、停止済み対象外/0件説明の現実操作。 |
| Q11 running/queuedをbulk pause | 実装済み | 旧J queued12→paused、CPU選択保護 | J/JobService同一 | runningを含むbulk、対象0件の説明/可否。 |
| Q12 固定件数/不可逆/取消確認、媒体保持 | 確認文修復済み | 旧J13件確定削除/11receipt保持。C326は旧選択保護test等 | Jは326以後同一 | 修復後の固定件数・不可逆説明・取消/再確認/確定の実操作が未結合。Q19確認と混同しない。 |
| Q13 タイトル/ID・分類/回数・全文理由、53px設計、真0件区別 | 一覧実装済み、寸法差あり | 旧F全文理由/8件頁/0件区別、E331の17件・2頁目・除外後0件 | F変更、M不変 | padding4px（原3px）、上段wrap、53px基準行の実測不足。長文を高さ固定で切る要求にはしない。 |
| Q14 優先順を持つ6理由分類、件数/AND | 実装済み | 旧FとE331のformat3件/all、全件数保持 | F変更、M同一 | 残5カテゴリの実クリック対象集合。媒体種別「形式」と混同しない。 |
| Q15 タイトル/ID部分一致、AND、解除/頁 | 実装済み | 旧F ID/解除/不一致、旧Mタイトル一致。E331は検索試験を追加していない | F変更、M同一 | 現GUIのタイトル/ID/解除/頁resetの結合。既存検索モデルを理由なく作り直さない。 |
| Q17 ページに依存しない一致全件retry、個別失敗後続継続 | 50件切捨て修復、illust/manga全一致実装 | C326/C327/C339の53行（52受理+1失敗）/260行、画面移動・明示取消。旧Mは一致1行だけ | S/F/batch一致331・339 | 53行試験は受理境界のfakeで実保存53件ではない。現GUI多頁batch/receipt、novel/ugoira、326独立指摘の後続再確認。認証/429/未知共通障害で中断する仕様は保持。 |
| Q18 固定1行だけ削除、他行/媒体保持 | 管理側の永続除外として実装済み | E331の取消/17→16/他client stale拒否/再起動、元合成DB不変 | S/F/A一致331・339 | 元DB物理削除は行わない保全仕様。実装後の独立レビュー承認は今回未回収。339起動経路への結合は別枠。 |
| Q19 フィルタ外を含む全件数確認、取消/クリア | 管理側の永続除外として実装済み | E331のfiltered3→全16確認/取消/確定0/再読込・再起動0 | S/F/A一致331・339 | 同一sourceの4runを反復する必要なし。独立レビュー/339起動経路への結合が残る。原DBは不変。 |
| Q20 一致全件7列/BOM/CSV、件数・保存導線 | 実CSV・開始通知とも実装済み | E331の2頁目から17行、format3行、全列/BOM/式無害化/開始通知 | F/CSVモデル一致339 | 開始通知は保存完了通知ではない。最新native保存ダイアログ等の受入と独立レビューは別。旧「成功件数通知なし」は解消。 |

## 4. sourceの結合と最新SHA

339固定候補は175入力、集合SHA `66eaafa3318c858266bd6f62efa5051c51ad51899aa330e79c9f8b917a932b61`。
manifest実SHAは `16ae4d421b97b42547ce6f45edad38245d568aab3cb3453417535525035cbe96`。
326固定集合は172入力、`b659967c4fbecf7c9ca2bbe68cf292c922bc0b322bd0b3a7c0f8acd1accda4c7`。

E331のsource-before/afterは175件同一。ただしこの175は169製品source+6checker入力であり、339の175配布入力と同じ集合ではない。共通169件は165同一/4変更。差は `bootstrap.py` と `ui/learning_view.py`、`ui/ranking_view.py`、`ui/recommend_view.py`。339には `native_instance.py` 等も追加されている。Q本体の同一性を利用できる一方、起動/配布全体の同一性を主張しない。

以下のsrcパスは `src/pixivvault_nicegui/` 相対。現在ファイルのhashを実取得した。331原票・339固定manifestの該当値と一致する。

| path | 現SHA256 | 324/326からの差 |
|---|---|---|
| ui/job_views.py | 093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 | 324 `44cc784f…` →326でQ03/Q12変更、以後同一 |
| services.py | 93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0 | 324 `85004d30…` →326 `f76101f5…` →後続retry/削除接続 |
| ui/failed_records_view.py | 1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de | 324 `e95b0684…` →326 `105334ac…` →後続取消/除外等 |
| job_service.py | 2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335 | 324/326同一 |
| failed_records_state.py | 652fefe2a6724b950ed73d821796dc337cbe6956c1c2b65d916491f246c7a63b | 324/326同一 |
| failed_records_repository.py | d2fa23b30b8f04dc0b48755b8c247e61cd86c35f16ebba38b53c5f81bd9b5270 | 324/326同一 |
| ui/theme.py | a9bbd5fc3d439b3b8b9ef994f6a2775517a37c4bbbc03bf4300eed131aa2318d | 324/326同一、Q01 CSS差は残る |
| failure_record_actions.py | cb3f4ebb34551f1f6ff4caed630ed45bf76325f979f179d78f8007b33c09abc5 | 326固定後に追加 |
| failed_record_dismissals.py | d4e202e5cb5027bd4991b8e625245285c5f8bdaf9c140672fdb6db5a5609768a | 326固定後に追加 |
| legacy_retry_batch.py | 34248b95601cb0863f1d8e1d238bad15f8639af5d491f2827559b8a3b68d7cbf | 326 `59c982c2…` から変更 |
| legacy_retry_errors.py | 30970570f435044da144cd107f00335f7e223e8af68e71202817ea5d1ba12f4a | 326同一 |

テストの現SHAも339full source-beforeと一致:
`tests/test_job_views.py` = `d4d5199923bcef28acb91bb09b5f5744fadd5806f60ee51b040f4786b994a663`。
`tests/test_legacy_failed_managed_retry.py` = `4fd2077bd4bfdd5136ae7a69f96bd8d158eb3d9f1b0d45e220831502d31613c1`。
339fullのsource-before/afterはともに `cf4ee2578194c3798f770b4af8d37e35c32f20a3020c5748138be2c4ab31a2a6`。

## 5. 自己判定と独立レビュー

`artifacts/p3-326-final/opus-review-r1.json` は別セッションの静的独立レビューで **Request Changes**。Q17には作品単位の失敗が全体を止める、未完了の別intent衝突、画面移動でbatch中断などの指摘があった。

現在は `services.py:2001–2048` で `ArtworkUnavailable` と既存intent衝突を `LegacyRetryRowUnavailable` に変換し、`failed_records_view.py:423–539` はbatch専用generation・明示取消・個別失敗継続・共有障害中断を使う。C327/C339で関連CPU試験を回収した。これは指摘への実装/試験対応であり、独立レビューの最終再承認を取得したという意味ではない。326の非Windows hash/lease等を含む全指摘の閉鎖は本書の範囲外。

`artifacts/p3-326-final/q18-draft-claude-r1.json` は名前にClaudeを含むが、内容は下書き2ファイルの作成者報告。「独立レビュー・UI接続・実試験は未着手」と記載されている。独立APPROVEとして利用しない。

`artifacts/p3-339-native/opus-high-review-r1.json` は `error_max_turns` / `Reached maximum number of turns (8)`、最終判定なし。scopeもNative/URI限定でQ群承認ではない。今回回収した範囲には、現Q修復全体に対する独立最終APPROVEはない。

worker-7の今回の作業は、実装担当とは別の証拠/限定source照合。ただし全sourceの独立コードレビューや実GUI受入を新たに実施していないため、その代用にはしない。

## 6. 次の有用な残作業（今回は未実施）

1. Q03/Q12を再実装せず、既存fixtureでmanaged作者名/ID/解除/再起動、固定件数/不可逆説明/取消/確定を同じ実操作へ結ぶ。N339デモ検索、Q19の確認を流用して完了にしない。
2. Q17の326指摘→現source→C327/C339を対応付け、Q18/Q19の実装後sourceとE331を独立レビューへ渡す。下書き作成者の自己報告から最終承認を作らない。E331と一致するQ本体の再試験は、具体的な新差分/懸念がない限り反復しない。
3. 機能残件はQ01の原行契約、Q07/Q17の旧失敗novel/ugoira、Q17の現GUI多頁再試行/保存結果。Q02/Q04–Q06/Q09–Q11/Q13–Q15は表の具体的欠測へ絞る。原152の別群やGPU独立レビューへ移る際はprimeの最新割当を受け、Native/URIと二重作業しない。

## 7. 原票の実SHAと文書の保全

以下は今回読取後の実ファイルSHA。全てroot相対。

| path | SHA256 |
|---|---|
| artifacts/p3-326-q03-q12-q20-impact-r1/result.xml | 5a2e8b3f3702406c5025d3b72580adfd0cabb14f0d729f28829e23d3699c0b36 |
| artifacts/p3-327-row-navigation-green-r1/result.xml | be1b2c4d1e34bfd71477aa46867bbbc0aecdb157e68585001800313aebc78c46 |
| artifacts/p3-329-failure-actions-r1/result.xml | 07d550ec9c92d32ed6b95d9e2c54bfa658d869c577a45b1c5fb14be0b67343a3 |
| artifacts/p3-330-native-and-failure-actions-green-r1/result.xml | 4cf28912a76aa4e75db5808ccfe597c70f3509423d6e62b6095a1ddfe539ae51 |
| artifacts/p3-331-failure-removal-edge-r1/result.json | d290412151dede76a76ae0e9dcae599e0f25dbd7b581aac34aa02c1d629aa2e7 |
| artifacts/p3-331-failure-removal-edge-r1/1280-round-1/all-17.csv | 8fa6fd4cc8d371833758bb2427a8ffe7c647569ea48397fada93debee3946746 |
| artifacts/p3-331-failure-removal-edge-r1/1280-round-1/filtered-3.csv | 718f6f9b1747bd467f4f346ff9713bf0844c56a40fa6108f36bbfc344e96305c |
| artifacts/p3-339-native/full-r1/run.json | c0b5aa3f7a03353822d8a8be22710d3f931a57eb8d37638c8a070517e54a41ff |
| artifacts/p3-339-native/full-r1/result.xml | ac4aac9c8f6f69cf7baebae7c0249502194840d4d295e7dc39c6237e6e6e7472 |
| artifacts/p3-339-native/jobs-packaged-r1/first.json | 85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d |
| artifacts/p3-339-native/jobs-packaged-r1/restart.json | e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854 |
| artifacts/p3-326-final/opus-review-r1.json | 8470ce1f0321f24d7df973a7ccc408530f6428b30d7b678bed1d2e3b4c1f9b31 |
| artifacts/p3-326-final/q18-draft-claude-r1.json | 0744a16b6f9b4c727091286d432b9707c743c7aedea273dfa4bc6ed373ea99fd |

入力文書は旧hashと一致し、今回変更なし:
`docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json` = `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4`。
`docs/P3_324_Q_ACCEPTANCE_MAPPING_2026-10-29.md` = `613a7c2e995072ac62a84a8c3efc7fbfcbf921fa2409a36a73c2e455d8d07881`。
新規作成直前、本書パスは未存在を再確認した。既存338/339結果を上書きしていない。

READ_END。本書以外の変更pathsは0。製品・test・checkerは未編集。実行した短い検索/metadata/hashコマンドは完了結果を回収済み。復旧後のagents messageはprimeへqueuedとなり、前回のAGENTS_NO_CHAT状態は今回再現していない。最終文書SHAとfinish送達結果はチャット側で返す。

## 8. 2026-10-30追記: p3-340 Q03/Q12 checker準備（GUI未実行）

この追記は後続の新しい割当による。上のREAD_ENDは証拠回収ターンの完了記録である。
今回の変更は新規 `tools/check_q03_q12_managed_browser.py`、新規 `tests/test_q03_q12_managed_checker.py`、本節の追記だけ。Q製品source、コピー元checker、元の152台帳は変更していない。

primeのNative/URI修復とGUI専有を維持し、GUI・server・Edge・Claudeを起動していない。実施したのは新checkerの準備と限定CPU試験だけ。GUI結果/正式Q03・Q12 PASSは追加していない。

### checkerに実装した実行契約

| 段階 | 検証対象 |
|---|---|
| 合成fixture | 明示した4行301/302/305/306の原SQLiteと静止コピー、合成PNG2枚を新規生成。ハンドルを閉じ、両DB/画像のbytes・mtime・SHAと正しい行集合を記録する。 |
| 初回Q03 | 旧失敗行の実ボタンから301/302を真のmanaged受理/SQLite/保存runnerへ通す。作者名「合成作者」、ID301、不一致、解除アイコンを実Edgeのclick/keyboardで操作し、表示job ID集合を照合する。 |
| 初回Q12 | 製品runnerの手前だけで305をcheckpoint付きで待機させ、306をqueuedにする。完了301・実行中305・待機306の3件を実checkboxで選択。固定3件、取消不可、実行中停止、保存済みファイル保持の文言を確認。取消ではjob/revision/保存bytes不変、確定では302だけ保持する。305の実JobCancelled/runner終了、306未実行を別に確認する。 |
| 再起動Q03/Q12 | 同じmanaged rootを通常終了後に再起動。302だけの履歴・受理済み作者metadata復元、作者名/ID302/不一致/解除を操作し、合成通信への呼出しも0件とする。残る1件の取消/確定を同じ確認経路で実施する。 |
| 保存証明 | 製品が作成した2receiptとPNGを読戻し、全bytes・SHA・寸法・pixelを照合。履歴削除前後/再起動後も全4ファイルの集合とbytesを保持。receiptの製品ラベル`pixiv-original`を実Pixiv GETの証拠にはしない。 |
| 隔離/終了 | 新規専用artifactのみ。fixture marker、nonce、loopback peer/Host/Originを検査。Python外向きsocketを拒否、Edgeを当該originへ制限。PID/birthをOwnedTreeで照合して通常shutdown、exit0・services.closed・子0・port解放を要求。強制killは行わず、閉じない場合は失敗を残して反復しない。 |
| 実行単位 | 1つのEdgeを再利用し、1280×720/1920×1080の各初回・再起動、計4round。操作時刻JST、確認文・選択ID/revision、画面、各roundの原票、sourceSHA前後、元DB/画像SHA前後を保存する。 |

既存`check_failed_managed_retry_browser.FakePixiv`と、`check_library_calendar_slider_owned`の所有観測/loopback拒否/新規保存、`run_browser_checks`の実行環境/health/port確認を再利用した。`stop_child`のterminate/kill経路は使わない。元checkerのソースはそのまま。

### 今回の限定CPU結果

- `artifacts/p3-340-q03-q12-worker7-cpu-r1`: 4 PASS / 1 FAIL。新testが渡したowner文字列が製品の32桁hex契約外だった。製品は変更せずtest fixtureを修正し、失敗原票を保持した。
- `artifacts/p3-340-q03-q12-worker7-cpu-r2`: **5 PASS / 0 FAIL / 0 ERROR / 0 SKIP、exit0**。実開始2026-10-30 11:45:56.1425331 JST、終了11:45:57.5445191 JST。
- この5件はfixture改変検知/保存先境界、ASGI peer・Host・Origin/nonce/redirect拒否、正常終了判定の負例、実ApplicationServices/JobServiceの2作品保存・実行中取消・待機未実行・履歴削除後bytes保持・再起動metadata/合成呼出し0を検証する。最後に合成保存画像を意図的に壊し、checkerが検出することも確認した。
- CPUでは製品serviceを直接呼んでfixtureと保存oracleを検証した。ブラウザのclick/keyboard、dialog表示、server/Edgeの正常終了を実行済みとはしない。

| path | SHA256 |
|---|---|
| tools/check_q03_q12_managed_browser.py | 712bc7eaec5d97d39c154fee8d429b461c9954b802309620218aa3336693f6b7 |
| tests/test_q03_q12_managed_checker.py | cdc8eef15a1b147c6b10b5ca237ee3848bd2a7d5960fa1771bf67257b326e282 |
| artifacts/p3-340-q03-q12-worker7-cpu-r1/result.xml | 14896d0d91636cb22246d48d7a2e757628cac2a82614324f321fca5223a82098 |
| artifacts/p3-340-q03-q12-worker7-cpu-r2/result.xml | d850b8f3d11666572604c1825fa7615285b9ff0e2ea856dc9c2f623db56bf3f7 |

変更前記録は`artifacts/p3-340-q03-q12-worker7-prep-r1/before.json`、終了時source照合は`after-code.json`。本書の追記前bytesを同directoryの`P3_338_Q_EVIDENCE_RECOVERY.before.md`へ保全した。Q関連製品4ファイルと再利用helper/source5ファイルは開始時hashのまま。

### primeのsource固定・GUI実行許可後のコマンド（今回は未実行）

workdirは本書と同じroot。実行環境は既設runtimeを使う。新規outputが既存なら別の末尾を割り当てる。checkerは既存outputを拒否する。

```powershell
$pvQRoot = 'S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028'
$pvQPreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = "$pvQRoot/src;$pvQRoot"
    & "$pvQRoot/artifacts/p3-318-runtime/release-01/venv/Scripts/python.exe" -B "$pvQRoot/tools/check_q03_q12_managed_browser.py" --headed --output "$pvQRoot/artifacts/p3-340-q03-q12-worker7-edge-r1"
}
finally {
    $env:PYTHONPATH = $pvQPreviousPythonPath
}
```

残coverage: 上の実Edge4roundは全て未実施。Windows packaged/native/DPI比較、Q03の多頁から検索した際の先頭頁復帰、確認中に他clientが対象を変える競合、媒体転送途中そのものの取消、他Q群はこのcheckerの新規実測範囲外。Q12の実行中取消fixtureは実保存runnerに入る直前のcheckpointであり、既存2作品の保存は実managed writerで完了させる。Q17のfake受理を実保存と読み替える経路はない。

この準備とCPU結果は実装者の自己検証であり、独立レビュー承認ではない。primeのNative修復後のsource固定と新しいGUI実行許可を待つ。
