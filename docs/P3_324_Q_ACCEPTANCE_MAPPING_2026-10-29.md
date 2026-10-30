# P3-324 Q群受入対応表 — 原ID・原期待を固定

調査: 2026-10-30 JST（指定されたファイル名の日付2026-10-29は維持）。担当: CoS worker-7。
作業ツリー: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028。
基準HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba / phase11/p3-vault-cleanup-race-20261028。HEADはdirty全体を表すhashではない。

本書は準備と限定読取の結果である。実在QはQ01–Q15/Q17–Q20の19操作。Q16は独立行ではなくQ07に旧Q16の入口が統合されている。原ID、操作名、v8台帳、公式集計33/43/76を変更していない。正式PASS追加は0。GUI・pytest・API・Git操作、新checker作成、製品編集は行っていない。session85978の全CPU回帰に対する操作も行っておらず、実行制限解除の連絡を前提にしていない。

## 1. 判定の要点

- 旧J（合成ジョブ）、F（旧失敗一覧/CSV）、M（旧失敗→managed再試行）の成功原票が別worktreeに残っていた。現作業ツリーのartifactsに見つからないことは、未実施の根拠にならない。
- 旧証拠で閉じられるのは、記録済みのブラウザ操作と保存結果、および同一hash/同一関数に限定した変換・制御の契約である。旧成功を現候補の全条件PASSへ読み替えない。
- Q01の行デザイン、Q03のmanaged作者名検索、Q07の通常queue入口・旧失敗側の対応媒体、Q12の確認文、Q17の一致全件処理、Q18/Q19の削除、Q20の成功通知にはsource上の差がある。これらは同じcheckerを反復しても閉じない。
- Q02/Q04–Q06/Q08–Q11/Q13–Q15には既存機能・旧観測がある。主な不足は現候補への接続、未観測の状態/入口/通知/見た目であり、台帳nullだけを未実装と扱わない。
- 保護する元DBの書換えを必要条件にしない。Q18/Q19等を成立させるには専用管理側の記録/除外状態など、元DB不変の製品経路が必要。これは今後の実装案であり、実装済み・承認済みの仕様変更とは記録しない。

## 2. 参照した原条件

原版は S:/Projects/PixivVault。raw SHAと行を照合した。gui.pyはv8記録どおりrawとHEADの改行形式に差があり、rawを原HEAD bytesと同一扱いしない。

| 参照 | 今回確認した箇所 | 用途 |
|---|---|---|
| docs/UI_GUIDE.md | 70–81 | 7状態、進捗/ID、専用ログ、一括操作、失敗リスト、CSV/再試行/削除 |
| docs/QUEUE_FAIL_ROW_LAYOUT_SPEC_2026-08-30.md | 28–44、75–110 | 3px共通注意帯、36px/53px設計、全文理由、1px区切り |
| docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md | 36–41、54–61 | queue作者名検索、失敗の6理由分類、タイトル/作品ID検索 |
| gui.py | 2911–2939、3030–3176、3229–3394、3450–3469、3647–3659 | queue表示/個別・一括操作/検索/更新 |
| gui.py | 4080–4342 | 分類優先順、検索AND、8件頁、失敗行、再試行、削除、CSV |
| server.py | 1409–1473 | 未完了削除時のstop、pause元状態、resume、stopping遷移 |
| docs/UI_SWEEP_CHECKLIST_2026-09-03.md | U-8、140行の操作と期待のみ | 押した直後の受理表示、停止完了待ちとの区別 |
| 移植側 docs/BASELINE.md | V11 | DL開始/停止/再開。元の開始fixtureは定義していない |

原資料間の差を保持する。

1. Q01: 8/30設計の「進捗/IDをtooltipへ退避する案」は未確定案。固定gui.py3034–3044/3138–3147はN/Mの96px席とIDの192px席を常時表示へ戻している。tooltip案へ原期待を縮めない。
2. Q03: 原コード3450–3469はauthor_nameがあればその名前、無ければkind/id表示を小文字化して部分一致。正式indexは「作者名・ID」。原に存在しなかったID同時検索を原実装そのものとは呼ばず、正式期待は維持する。
3. Q07: 正式indexは「旧Q16、queue/failed双方の入口、attempt保持」。しかし固定gui.py3047–3055の通常queueにはpause/resume/stop/deleteしかなく、retryは失敗リストの入口。参照矛盾を残す。worker判断でどちらかを削除しない。
4. Q17: 原コード4288–4309はフィルタ一致全件をページ分割前にcaptureし、個別例外後も残りへ進む。現上限50と最初の例外で終了する構造を原全件と同等とはしない。

以下のfixtureは原文からの逐語転記ではない。既存checkerと原コードに基づいて本調査で具体化した試験開始状態である。

## 3. 有効な開始fixtureと証跡

### J: 既存合成ダウンロード/queue

tools/check_download_jobs_browser.py28–76/84–183/186–268。

新規output配下の専用download_sandboxのみ。架空120作品からdemo-0001..0011を選択して11件を投入。demo-0002はattempt1だけ合成失敗、demo-0003とdemo-0012はcheckpoint付きEventで待つ。作者名はfixtures.pyの「架空作家 01..12」で循環。3はrunningを保持でき、4はqueued、2はerror、先行1はdoneを観測できる。各状態は必要な時点で観測し、7状態が同時に揃うとは仮定しない。

既存順序: DL選択/投入 → queue頁送り → 3検索/pause → 他タブ/復帰 → resume → queued4のstop → gate解除 → failedタブで2をretry → queueでdone/attempt2 → 履歴delete → 12..24を追加し12をgate保持 → queued13..24の12件を頁越え選択 → bulk pause/resume/stop → gate解除 → stopped13件を確認delete。実ブラウザのclick/fillであり、物理マウス/キー、nativeの同操作を代用する証明ではない。

- 原票J: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json
- 1920×1080/1280×720ともsuccess。初回11jobs、8件頁、retry_attempt=2、個別delete_kept_files、12件の頁越え選択/一括pause-resume-stop、13件確認削除、再起動履歴を記録。
- verified_receipts=10は個別操作後の値。bulk後saved_receipts_preserved=11と混同しない。今回1920側に残る11receipt原文を読取: demo-0001..0012のうち0004以外、計19頁のmanifest。実画像bytesの再検査は実行せず、bytes/SHA一致は旧checkerのassertとして扱う。
- 各viewportのnormal_exit/port解放=true。console/page/foreign requestは空。今回新たにPID/portを起動・終了したという意味ではない。

### F: 既存旧失敗一覧/CSV

tools/check_failed_records_browser.py26–36/98–210、src/pixivvault_nicegui/failed_records_fixtures.py6–17。

新規生成SQLiteのfailed_queue7列と生成画像だけ。failure-0001..0017の17行、failed_at降順、8件頁。先頭理由は2行、最後のtitleは=SUM(1,2)、retry_countは0/1/2。元DBは使わない。refresh故障→復旧は、この専用生成DBをcheckerの明示writerだけが変更する既存試験。

| カテゴリ | 正しい件数 | 合成work_id末尾 |
|---|---:|---|
| communication/通信 | 3 | 0001、0007、0013 |
| not_found/見つからない | 3 | 0002、0008、0014 |
| permission_age/権限・年齢制限 | 3 | 0003、0009、0015 |
| save_dest/保存先 | 3 | 0004、0010、0016 |
| format/形式 | 3 | 0005、0011、0017 |
| other/その他 | 2 | 0006、0012 |

- 原票F: S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/result.json
- 2寸法のsuccess、17件/8行/3頁、2行目の理由、DOM/scrollのタブ保持、2client独立、検索/形式カテゴリ、故障→前表示保持→復旧→0件、normal_exit/port解放/forced_cleanup=falseを記録。
- そのdirectoryのall-1280.csv/all-1920.csvは各2198 bytes・同一SHA。filtered-1280.csv/filtered-1920.csvは各431 bytes・同一SHA。今回CSV原文とhashを読取。
- 旧checkerは実downloadイベント→save_as→bytes読戻し、UTF-8 BOM/7列順/全17行の全フィールド/形式3行をassertしている。2頁目でのexportも17行であり、現在頁のみの出力ではない。
- 最後のtitleだけCSVでは'=SUM(1,2)へ無害化される。全セル無変換一致とは呼ばない。
- retry/delete/clearがdisabledであることは旧DB保護の証拠であり、Q18/Q19の削除成功ではない。

### M: 既存旧失敗→managed再投入

tools/check_failed_managed_retry_browser.py30–78/84–107/110–190。

静止した合成failed_queue4行: 301 illust/Timeout/retry_count1、302 manga/404/0、303 novel/403/2、304 ugoira/Timeout/0。合成作者23。FakePixivは301/302だけに合成120×84 PNGを返す。外部通信を行わないdoubleであり、receiptのsource="pixiv-original"という製品ラベルも実Pixiv GETの証拠にはならない。

- 原票M: S:/Projects/PixivVault-NiceGUI-wt/failed-queue-optin-retry/artifacts/failed-managed-20261023/edge-final-terminal-retry/result.json
- 2寸法×初回/再起動の4観測。301を個別再投入→done1→完了後の別クリックでdone2、タイトル「合成漫画」で302だけへ絞って一括再投入→done3。
- 2作品の実decodeとpixel一致、2receipt、再起動GET0、4回normal_exit/port解放、browser errors/foreign request空を記録。今回1280側の2receipt原文も読取。
- 旧失敗4行は保持される。novel/ugoiraはdisabled。一括で「複数頁の全件」を試した原票ではない。

### 原欠測用の追加fixture案

既存runへ観測を追加するための設計であり、作成/実行済みではない。

- Jのcheckpoint gate中に各状態チップ、作者名「架空作家 03」、ID「demo-0003」、解除を観測する。個別pause→resumeの既存場面へ受理文を追加。一括削除の既存場面へ件数/不可逆説明→取消で不変→確定を追加する。paused状態からのstop、未完了deleteはcheckpointで確実に保持した対象を別の短い分岐に用意する。
- Fは既存17行をそのまま使える。未観測5カテゴリ、タイトル検索、空一致から「すべて」へ戻る操作、行表示/寸法、CSV成功通知を同じrunへ追加する。6カテゴリが存在するだけで全6分類をクリック済みとしない。
- MのQ03不足は既存301/302のdone状態で、queue検索へ「合成作者」と「301」を入力するだけで確認できる。新しいAPI/画像fixtureは不要。
- Q17の全件を修復するなら、同じM用試験で対応51行＋非一致1行＋novel/ugoiraの対照を用意する。例: 合成701..751をtitle「Q再試行対象 NNN」、user_id23、illust/manga、各1頁とし、752だけ検索不一致、753/754を非対応媒体にする。FakePixivもこの合成ID集合だけへ拡張が必要。既存FakePixivは301/302以外を拒否するため、現4行fixtureを反復しても埋まらない。51行なら8件頁越えと旧50上限を1回で検査できる。上限未修復のまま9行だけを通して一致全件PASSとはしない。
- Q18/Q19は製品の管理側削除経路が存在してから、F相当の17行で1件削除/フィルタ3件表示中の全17件確認/取消/確定/再読込を観測する。元snapshotと媒体は不変を確認する。試験側でdisabledを解除する方法は不可。

## 4. 原ID別対応表

J/F/Mは上記の原票とfixture。各「操作」は現候補で補完するときの実クリック/文字入力の順であり、今回実行した操作ではない。入力欄はマウスでfocusし、Ctrl+A→入力、解除アイコン/ボタンは実クリックで行える。既存証拠の操作方式はclick/fillで区別する。

| 原ID・操作名 | 原期待と根拠 | 有効開始・具体操作 | 旧観測で閉じられる部分 | 現候補で必要なもの／判定 |
|---|---|---|---|---|
| Q01 ジョブ状態・進捗一覧を表示する | UG70–75、gui3030–3176/3647–3659。7状態/初回表示/自動更新、進捗/ID、専用ログ。36px設計、注意帯はpaused/stopping/errorのみ同色、形で区別。 | Jでqueueを開く→頁送り。gate/操作による各状態変化を同画面で観測。N/M・作者/作品ID・理由・進捗バー・操作可否を対応付ける。 | Jの8+3頁、running/paused/stopping/stopped/done/errorの遷移、件数更新。7状態すべての行表示/形/寸法/ログを検査した証拠ではない。 | **実装差**: theme173–177はmin-height56px、背景、全状態3px帯と状態別色。_make_rowに原の状態アイコン/破線描画がない。これはCSSの差で、実測行高を56pxと断定したものではない。原36px/96px進捗席/192pxID席と専用ログを閉じる必要。修復後同runでgeometry/視覚を回収。 |
| Q02 状態別にキューを絞る | gui2911–2939/3450–3469。全件+7状態、検索AND、変更時先頭頁。 | Jの各状態が存在する時点で対応チップをクリック→期待ID集合/件数→検索併用→解除。 | Jはqueued/paused/stopped/allのクリックと件数、頁越え選択を確認。 | **証拠不足**: running/stopping/done/errorチップの実クリック後の対象集合、検索AND/頁reset。model/UI経路は存在。個別の新checkerは不要。 |
| Q03 キューを作者名・IDで検索する | 正式index、原gui3379–3394/3450–3469。名前部分一致/ID、解除、先頭頁。原fallback範囲の差は§2保持。 | Jで「架空作家 03」→「demo-0003」→解除。Mのdone301/302では「合成作者」→「301」→解除。 | Jはdemo-0003/0004/0002のID検索とタブ復帰でquery保持。作者名の実操作証拠はない。 | **managed側の静的欠落＋観測不足**: _matching423–428はservices.artworksの作者mapのみ。services300はdemo一覧、1679–1680はmanaged jobにselection.title/IDを格納。Mのtitle「合成作品301」/ID301から「合成作者」は一致せず、作者metadataが検索へ接続されない。GUI未再現だがsourceで限定した欠落。demo成功で実managed作者名を代替しない。 |
| Q04 個別ジョブを一時停止する | SW U-8、BASELINE V11。押した直後の受理表示と停止状態。 | Jのrunning3に一時停止→受理文とpaused→タブ往復。 | Jのrunning→paused、タブ復帰後paused/query保持。 | **証拠未結合**: command/制御本文は旧と同一。受理文の時点を旧checkerはassertしていない。現接続とUI応答を既存クリック直後へ追加。 |
| Q05 個別ジョブを再開する | SW U-8/V11、server1450–1461。paused前がrunning/queuedならその状態へ戻す。stoppedは再開しない。 | Jのpaused3へ再開→running。queued由来pausedはbulk12件の既存分岐を使う。 | Jの3 running復帰、bulk queued復帰、stopped4再開disabled。 | **証拠未結合**: 現candidateの受理文/接続。既存paused_from制御を理由なく全件再試験しない。 |
| Q06 個別ジョブを停止する | SW U-8/V11、server1463–1473。停止要求受理→stopping→stopped、pauseを解除。 | Jのqueued4で停止。追加はcheckpoint中のpaused対象を停止→完了と次の処理の進行を観測。 | Jのqueued4 stopping→stoppedと再開不可。 | **具体的欠測**: SWのpause→stop順序は旧Jにない。受理と完了を分けた通知、pausedからの終了を短い追加分岐で確認。 |
| Q07 失敗したジョブを個別に再試行する | 正式indexの両入口/attempt、UG77–81、gui4199–4245。入口矛盾は§2。 | Jのfailedタブでerror2を再試行→queue done/attempt2/receipt。M301を再投入し終端後に再クリック。 | Jはattempt2/実保存。Mは別明示クリックで2つのdurable jobs、原DB保持、2作品decode/再起動GET0。 | **実装差/参照差**: 通常queue523にはretry無し（元固定コードにも無し）。現managed旧失敗はillust/mangaだけ、元行のretry_count/消込は更新しない。両入口・媒体全体の完了にはならない。再失敗時の回数/理由通知も未観測。原IDを変更せずprimeで参照差を扱う。 |
| Q08 ジョブ履歴を削除する | UGqueue、gui3229–3257、server1409–1431。対象履歴だけを除去し、未完了なら停止、媒体を保持。 | Jのdone2を削除確認→除去。未完了対象では取消→確定→queue不在/処理停止、別receipt保持。 | Jのdone2除去、既存receipt path集合保持。bulk後のbytes/SHA検証あり。 | **具体的欠測**: 未完了deleteの実UI、取消、schema2後の履歴復旧/非再実行。旧job control本文は同一でも永続化/終了復旧が変わっている。 |
| Q09 ジョブを一括停止する | gui3259–3331/server1463–1473。選択/対象状態、要求と結果件数、busy解除。 | Jでqueued13..24を全件選択→次頁4件選択済→停止→stopping→stopped。追加はrunning/pausedを含む選択集合。 | Jの12件選択/停止、gate解除後停止13件、選択解除。 | **具体的欠測**: mixed running/paused/queuedの対象と非対象保持、受理/拒否/失敗件数表示。旧成功のqueued12件を反復するだけでは埋まらない。 |
| Q10 ジョブを一括再開する | gui2918–2925/3267–3278、server1450–1461。pausedだけ再開、元状態保持、停止済みは対象外。 | Jのpaused12件を選択→全件チップへ切替（選択保持）→再開→queued12件。 | Jでその経路とselection0、個別stopped再開disabled。 | **証拠不足**: running由来pausedを混ぜたbulk復帰/対象外理由。現接続を同じrunの短い分岐で確認。 |
| Q11 ジョブを一括一時停止する | gui3267–3273/3295–3331、server1435–1448。running/queuedをpaused、prev_state/件数/busy保持。 | Jでqueued12件を頁越え全選択→一時停止→受理12/paused12/選択0。running対象を含む場合も確認。 | Jのqueued12件は成功。 | **証拠不足**: runningを含むbulk、操作可能0件の理由/disabled。個別running pauseの旧結果はbulkを直接証明しない。 |
| Q12 ジョブを一括削除する | UG73、gui3333–3371/server1409–1431。全件/選択集合を確認前に固定、件数・取消不可、取消、媒体保持。 | Jのstopped13選択→削除確認の件数/説明→取消で不変→再確認/確定。 | Jの13件確認削除、11receipt保持、10履歴残、選択0。 | **確認文の実装差**: _bulk_delete_event448–454に件数/不可逆説明がない。旧Jは取消/文言をassertしていない。修復後既存delete場面に追加。全状態選択の対象集合と未完了停止も不足。 |
| Q13 旧失敗リストを表示する | UG77–81、行契約B95–106、gui4199–4286。タイトル/ID・分類/回数、理由全文、53px設計基準/区切り、真0件と不一致の区別。 | Fで失敗タブ→3頁を送る→行と元7項目を照合→タブ往復。 | Fの17件・8行/頁、2行目の理由、DOM/scroll保持、0件/不一致/読取エラーの分離。 | **表示証拠不足＋設計差**: current CSS padding4px（原3px）、上段wrap、53px実測未結合。全行の回数/理由の表示対応をCSV一致で代替しない。長文を53pxへ強制クリップせず基準行と長文を分けて測る。 |
| Q14 失敗理由カテゴリで絞る | 原分類5優先規則+その他=6、gui4082–4097/4151–4180。形式は媒体種別ではない。 | Fの各チップを順にクリック→§3の件数/ID→検索併用/先頭頁→全件。 | Fのformat3件と正しいCSV対象、all17件保持、AND、タブ選択保持。分類関数は旧と完全hash同一。 | **残5分類の実表示未観測**。priority/未知語のコード同一性は再利用する。原条件に必要なチップ/対象集合だけを追加し、無関係な新分類を追加しない。 |
| Q15 失敗リストをタイトル・ID検索する | gui4111–4125/4151–4186/4263–4286。タイトル小文字化/ID部分一致、カテゴリAND、先頭頁、解除導線。 | FでID1→clear、タイトル「架空の失敗作品 00」→頁reset、format+absent→検索解除/すべて。 | FはID/不一致/clear/AND、Mはタイトル「合成漫画」で302。別fixture・別原票である。 | **証拠未結合/追加観測**: タイトル検索をF現接続へ結ぶ、頁resetと「すべて」の復帰。検索モデル/handlerは旧同一。 |
| Q17 失敗項目を一括再試行する | UG77–81、gui4151–4173/4288–4309。一致全件を頁に依存せずcapture、再失敗件数と一覧更新。 | M現4行で漫画1件へ絞り→一括再投入。原全件の修復後はM51で2頁目から実行→51件/非一致0件/対応外区別/保存結果。 | Mの一致1件受理とdoneは証明済み。複数頁/全件の証明にはならない。 | **実装差**: request_retry245–249は対応先頭50件、_retry_records305–338はloop外exceptで最初の失敗後残りを処理しない。旧行不変で再押下しても後続51件目へ自動で進む根拠はない。novel/ugoira未対応。51件/部分失敗のfixtureと製品経路が不足。 |
| Q18 失敗記録を1件削除する | UG81、gui4221–4225。固定work_idだけ除去し再読込、他行/媒体保持。 | 管理側削除経路ができてからF相当17行の1行削除→16行→再読込。原snapshot/媒体不変。 | Fは行delete disabledのみ。 | **未実装がsourceで確定**: current view230–231は常にremove.disable、削除handlerなし。Q08のjob履歴削除は別機能。反復GUIで閉じない。 |
| Q19 失敗記録を全件クリアする | UG80、gui4311–4327。フィルタにかかわらず全件数、確認/取消/確定、作品保持。 | 管理側経路完成後、format3件表示中→全17件を示す確認→取消→不変→確定→0件/再読込。 | Fはclear-all disabledのみ。 | **未実装がsourceで確定**: current view91–92のmenu disable、clear handlerなし。元DB書込許可を追加せず専用管理側で成立させる必要。 |
| Q20 失敗リストをCSVへ出力する | UG80、gui4151–4173/4329–4342。一致全件7列・BOM、成功件数/保存先または失敗表示。 | Fで2頁目からmenu→CSV実保存→17行全列。formatに絞り→CSV3行、成功表示を観測。 | F原票/実CSV4本でファイル名、BOM、列順、全行/改行、式無害化を確認。state/csv_bytes/export本文は旧と同一。 | **変換部分は再利用可能、通知は実装差**: current export365–375はdownload指示とmenu close、例外文だけ。成功件数通知がない。保存先はbrowser/native保存導線へ委譲されるため、原cwd保存先と同一とは言わない。修復後同export場面へ通知/保存結果を結ぶ。 |

## 5. 候補と旧証拠のsource結合

### 候補1fと324

| 候補 | 固定集合SHA | manifest SHA |
|---|---|---|
| artifacts/p3-321-native-gpu/frozen-01（169ファイル） | 1f7467974c52765ef718ac87492b5963df1411a047f72f9e39684b39a26e9a05 | 6f893ad53b602dfabf37e73ba52a4f938e93ee27f6d8af063bfdd5c0a83c146e |
| artifacts/p3-324-gpu-review-fixes/frozen-01（169ファイル） | 2da83a944f6bd19c7a0fcd6b5416bae78d002de2c0603dc6c71916e7e82e62b0 | 3ec18a952ddde8f3a8b53d034af02abcc52b7b2a7423f6c51410b0fc63700c9c |

1f→324は163同一/6変更/追加削除0。変更はlegacy_similarity_inputs.py、managed_downloads.py、managed_similarity.py、services.py、similarity_worker.py、ui/similar_view.py。

Qのui/job_views、ui/failed_records_view、ui/theme、job_service、failed_records_state/repository/fixturesは1f/324同一。今回liveのこれらとservices/managed_downloadsも324 manifestに一致した。製品全169を再hashしたという意味ではない。

Qに関連する6変更中2ファイルを凍結source同士で全文比較した。

- services.py653: catalog_snapshot() → catalog_snapshot(verify_pages=False)。既存保存済み一覧のscan経路の差。
- managed_downloads.py332/349/376/379–381/392: saved_receipts_with_dates、saved_receipts、catalog_snapshotにverify_pages=Trueを追加して伝播。既定はTrue。queue制御/失敗分類/CSVの差ではないが、Mの保存→library表示に接するためmanaged接続の確認は残す。
- Q目的だけの1f/324両方の反復実行は不要。実施するなら修復後にprimeが固定した現候補1つへ結ぶ。

### 旧J/F/Mと現候補

- J resultは当時49製品sourceのhashを保持。324と17同一/32変更。別のbuild-inputsは51入力であり、README/pyproject込みの数を49と混ぜない。
- 旧Jの残存job_views/job_serviceファイルhashはresult記録と一致した。現との差分ではUIの_matching/search/filter/頁/選択/個別command/bulk/確認dialogの関数本文は同一。主な追加は小説/うごイラ表示、退避export/進捗表示、テーマdark指定の除去。
- 旧JのJobService.control/enqueue/checkpoint/progressも関数本文同一。ただし永続化はschema1→2、job_history保存/復元、Windows長パス、終了/再起動時interruptedの扱いが変わった。履歴削除/再起動の現接続を旧PASSから自動継承しない。
- F resultはsource_changed=falseのみで、run内のsource集合を保存していない。同worktreeのbuild-inputs（48入力、324と16同一/32変更）は結果更新の約11分後であり、当該Edge runへの直接hash結合ではない。
- Fの旧残存view/repositoryはそのbuild-inputs hashと一致。failed_records_state/fixturesは現候補と完全hash同一。viewのsearch/select_category/change_page/export/_refresh/set_activeは同一本文、追加はmanaged retry接続等。repositoryにはimmutable読取/sidecar拒否/source署名が追加された。CSV変換/フィルタの旧証明は再利用し、現read→view接続を別に確認する。
- M resultはsource集合を保存していない。指定の結果・周辺artifactから当該runの直接manifestは回収できていない。現在の旧worktree hashを当時hashにすり替えない。native-copy-check-secondは通常UI/復帰の記録で、M再試行のsource結合を埋める証拠ではない。
- 指定3checkerは現ファイルと旧実行worktreeの残存ファイルがそれぞれ完全hash一致。これは手順の照合に使えるが、上のF/M runtime hash欠落を埋めるものではない。

### 関連hash

以下の「旧F」はrun直接結合ではなくbuild-inputsに記録された値。空欄はそのmanifestにない。

| path（src/pixivvault_nicegui/相対） | 旧J | 旧F build | 324（Q関連は1f同一。servicesのみ別） |
|---|---|---|---|
| bootstrap.py | 51ecb52d2472785ab0c003a39ff9810ffa5491f32885ee4c9c2bffb013415986 | b50c36f2ce462bf29f61e2dd0fc149ae26e93ab5ced39960cd13e6fb8c0975d8 | 34bbb4a51b4110f1fd100784dc208474672bc99acd9bb5f2744b7219cf46ea7c |
| services.py | 8bc1f55a7df5ec5cfe57c053f37555a51ef6c5a9a8079b4fbd7bd6ada6fbe1a6 | 11865bf8dcfbf6f068f869813d50d0d1603f89a1edf31ef8a34655b76f13aa34 | 85004d30b722c37bb5a7cf1c537d434834bc8fab40908c5d50a8b230edff8df6 |
| job_service.py | b9c4341840cede84ed0ca6a1d2d7626cb0fbd29e63e75ff61965469e37e95d63 | — | 2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335 |
| download_sandbox.py | 02376cce8a0f55e02b3dd91aa555ca6e07571fd90964c2628bcf98bdd512ecdc | — | 02376cce8a0f55e02b3dd91aa555ca6e07571fd90964c2628bcf98bdd512ecdc |
| failed_records_repository.py | d6a368873f1a38b0d014e570b9efe0e3c1c86f3152f0937e509ff23c5243356b | d6a368873f1a38b0d014e570b9efe0e3c1c86f3152f0937e509ff23c5243356b | d2fa23b30b8f04dc0b48755b8c247e61cd86c35f16ebba38b53c5f81bd9b5270 |
| failed_records_state.py | 652fefe2a6724b950ed73d821796dc337cbe6956c1c2b65d916491f246c7a63b | 652fefe2a6724b950ed73d821796dc337cbe6956c1c2b65d916491f246c7a63b | 652fefe2a6724b950ed73d821796dc337cbe6956c1c2b65d916491f246c7a63b |
| failed_records_fixtures.py | 1c6b446a3fa71a8c13999105ea39ed06500eb76ec2787999713bf2348929537e | 1c6b446a3fa71a8c13999105ea39ed06500eb76ec2787999713bf2348929537e | 1c6b446a3fa71a8c13999105ea39ed06500eb76ec2787999713bf2348929537e |
| ui/job_views.py | 1d323748440b349a7e335668600f81ad6ad8a1724b597b22e8b7a376a012d3a8 | — | 44cc784f19e4fd367e05c08f94223efdf4b9493d9cd1769e5912fd243dadf0b0 |
| ui/failed_records_view.py | e1ff3b5da06a850612216915963e48d8e7ee8ae912a99ba181b5ff1248bc7849 | e1ff3b5da06a850612216915963e48d8e7ee8ae912a99ba181b5ff1248bc7849 | e95b0684067d49884e061857cbcbaf7d4a43ee521cb3fe14eb0bcf403e3dc64f |
| ui/theme.py | f5c74c4afdea9d65385830028ca4b603daf428f54d292f4b5ecbff2954eff930 | b303df5831a64db04d463684b5d65bf57064ebec30f17a4e4fc2c4782f54f064 | a9bbd5fc3d439b3b8b9ef994f6a2775517a37c4bbbc03bf4300eed131aa2318d |

## 6. 次に行う最小実行

今回の範囲では実行しない。primeがCPU回帰の終了/所有を確認し、GUI実行禁止を解除した後、実装差を先に処理してから1つの現候補でまとめる。既存3checkerのCLIは次のとおりであり、各1回、msedgeのみが最小の既存一式。1fでも同じ一式を繰り返す必要はない。

指定3checkerだけでは§4の全欠測を検査できない。先に各既存checkerへ必要な観測を局所追加するか、同じfixtureのCoS実操作で補う。大量の新checkerを作らない。Q18/Q19をreadonly fixtureで反復する、M4行を繰り返してQ17全件とする、Q01のデザインを機能assertだけで通す手順は採らない。

実行workdir:
S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028

以下のPythonは存在をmetadataで確認しただけであり、今回実行して環境適合を認定したものではない。primeの検証済み専用runtimeが別なら、その既知runtimeを使う。OS/依存設定は変更せず、PYTHONPATHは当該実行プロセスで現worktreeのsrcへ向ける（既存設定を必要ならfinallyで戻す）。--outputは未存在の専用run名を使い、旧原票は上書きしない。

~~~powershell
$pvQRoot = 'S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028'
$pvQPython = 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe'
$pvQPreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = "$pvQRoot/src"

    & $pvQPython "$pvQRoot/tools/check_download_jobs_browser.py" --channel msedge --output "$pvQRoot/artifacts/p3-324-q-acceptance/jobs-edge-r1"

    & $pvQPython "$pvQRoot/tools/check_failed_records_browser.py" --channel msedge --output "$pvQRoot/artifacts/p3-324-q-acceptance/failed-records-edge-r1"

    & $pvQPython "$pvQRoot/tools/check_failed_managed_retry_browser.py" --channel msedge --output "$pvQRoot/artifacts/p3-324-q-acceptance/failed-managed-edge-r1"
}
finally {
    $env:PYTHONPATH = $pvQPreviousPythonPath
}
~~~

3コマンドは独立した実行単位。実際には1本ごとにexit/result/cleanupを回収し、成否不明のまま再送しない。上のブロック全体を一括実行する指定ではない。すべて失敗したら無条件再試験する手順でもない。

必要な追加観測を同じrunにまとめる順:

1. J: 修復したQ01表示 → 7状態/作者名・ID/解除 → pause/resumeの受理文 → queued/paused停止 →未完了delete → bulk mixed状態 → 既存bulk削除の件数/取消/確定 → 保存媒体/履歴の再起動。旧の合成保存アルゴリズム単体を再検証するための別pytestは追加しない。
2. F: 同じ17行で残5分類/タイトル/解除/頁 → 基準行と長文理由 → 既存CSV17/3の実保存と追加通知。削除機能修復後のQ18/Q19も管理側のfixtureへ接続してこの一式にまとめる。元snapshotは書き換えない。
3. M: 現managed再投入/保存/library/restartを既存の3jobs/2receipt経路で接続。作者名検索を同じdone状態に追加。Q17修復後の51件一致集合/非一致保持/partial failureを同一checker内の独立開始状態へ加える。媒体未対応を偽装PASSにしない。

上の既存3CLIに選択的な--case/--viewport等はない。存在しないフラグを提示しない。subsetだけを行うときは指定既存checkerの該当inspectを局所拡張するか正規CoS実操作にし、その方法を記録する。

### 次回の証拠結合で欠かせない項目

- 実開始JST、候補のsource-before/afterと集合hash、checker hash、出力先、操作したQ ID。現在の時刻やmtimeから旧実開始を推定しない。
- F/Mは現在のresultに実source集合を残さないため、run外の既存runnerで前後manifestを結合するか、その限定記録を既存checkerへ追加する。source_changed=falseだけを候補identityとしない。
- 受理表示、終端状態、保存receipt/CSV、通常終了exit、port解放は別の観測。UIからのclick送信だけを成功にしない。
- 原ID全条件が揃ったものだけをprimeが正式台帳へ結ぶ。本書は台帳のexpected_full/start_conditionを書き換えていない。

## 7. 原票・入力のSHA256

以下は今回の限定読取で取得した実ファイルhash。mtimeは結果ファイルの更新時刻であり、実開始/完了時刻の代用ではない。J/F/Mの原票の更新時刻はそれぞれ2026-10-23 09:02:21 JST、08:11:13 JST、20:21:00 JST。

| 実ファイル | SHA256 |
|---|---|
| S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json | 2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 |
| S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/build-inputs.json | 1a0c379a362fcecd62c4b5442a515954596e99059140cc110d9fb951457efac1 |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/result.json | 6b846b9ed96644ef1937e07a8bec210411d68ae883dd8a01d3fdcc2fbd4e48ab |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/all-1280.csv | 8fa6fd4cc8d371833758bb2427a8ffe7c647569ea48397fada93debee3946746 |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/all-1920.csv | 8fa6fd4cc8d371833758bb2427a8ffe7c647569ea48397fada93debee3946746 |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/filtered-1280.csv | 718f6f9b1747bd467f4f346ff9713bf0844c56a40fa6108f36bbfc344e96305c |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/filtered-1920.csv | 718f6f9b1747bd467f4f346ff9713bf0844c56a40fa6108f36bbfc344e96305c |
| S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/build-inputs.json | 1f7017689f6f138847c6397b53f21de3e43fa3a96265f8051b47eb6b6516c81d |
| S:/Projects/PixivVault-NiceGUI-wt/failed-queue-optin-retry/artifacts/failed-managed-20261023/edge-final-terminal-retry/result.json | df1f6dad2e6b4415b800bd0e5fc6fe535abf57885cfcfc2db0835dc6f143d967 |
| S:/Projects/PixivVault/gui.py | 731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939 |
| S:/Projects/PixivVault/server.py | a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0 |
| S:/Projects/PixivVault/docs/UI_GUIDE.md | 78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b |
| S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md | 065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28 |
| S:/Projects/PixivVault/docs/QUEUE_FAIL_ROW_LAYOUT_SPEC_2026-08-30.md | 472c0bd2c5d8e0dbd505f7bf59c15df8b1ecd45ed2b9414a2af62dd7a2929498 |
| S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md | 6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf |

| 現作業ツリー相対パス | SHA256 |
|---|---|
| tools/check_download_jobs_browser.py | 81be9cbc406840e4bba2e56fa0dfe95096f031a78696220c2dd088ca4ff25d61 |
| tools/check_failed_records_browser.py | 9b1fcce7c620a177502593b72fc511bf29b9bc48ad993bfea896d9e74c2d70a9 |
| tools/check_failed_managed_retry_browser.py | dec2d0615892c95afabe57e113b6292d850c19ba2042b25e129923cc83ba592c |
| docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json | 487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4 |
| docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md | 78fa125f87a82e241a983d4be7d90c5b49c4e1f702203e8b9500a47723c4d1b6 |

## 8. 所有と引渡し

変更するファイルは本書だけ。新規作成前に未存在を確認済み。src/tests/tools、既存台帳、原版、既存artifactsを編集していない。新しいGUI/server/試験プロセスを起動していない。今回の短いrg/metadata/hashコマンドは完了結果を取得した。session85978や他担当のPID/portを回収/停止していない。

primeへのagents action=messageは2回ともAGENTS_NO_CHATで未送達だった。これは会話identityに限るエラーであり、readonly化やCoSのファイル拒否ではない。別runを作って回避せず、指定scopeの読取と文書作成を継続した。最終finishの実結果はチャット側に報告する。

静的な独立照合ではQ07/Q13–Q20のchecker assert範囲、F/Mのsource hash不足、Q17の9件だけでは全件を閉じられない点、Q01のCSSと実測の区別、Q12確認取消を同じrunへ統合する方針を再確認した。GUI/全体の独立受入を済ませたという意味ではない。

