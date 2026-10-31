# P3-410 — Q13 真の失敗0件と「17件中検索一致0件」を本物Flet/NiceGUIで区別する

2026-10-31 JST。開始HEAD `610535c65991e4cc1260d4b14fe1bfba3127035d`、branch `phase11/p3-vault-cleanup-race-20261028`。元Flet `gui.py:4248-4285,4380-4383` とNiceGUI製品 `ui/failed_records_view.py:237-249` は、検索一致なし `一致する失敗はありません` と真の失敗記録0件 `失敗はありません / 失敗した取得は0件です` を異なる状態として扱う。以前の17件fixtureの検索一致0件とは異なる、**本当にfailed_queueが空の入力**を新規に実GUI受入した。

## 原本と入力保護

- 元Flet原本 `S:/Projects/PixivVault` の `gui.py` は読取のみ、SHA256 `731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939` で不変。
- 既存元33ファイルsource-copyはSHA固定・Win32 `FILE_SHARE_READ` で実行中OSハンドル保護。新 `tools/check_stage5_original_failed_empty_native.py` は、過去の17件adapterを使わず `builder_probe.FakeDB.get_failed_jobs() == []` をそのまま本物の `launch_true_gui.run()` へ渡す。これはFakePageの模造表示ではなく、`gui.py.main_window` の本物の全8タブをFlet Desktopで構築する元実装経路。
- NiceGUIは `tools/check_readonly_library_browser.py::build_fixture()` で新規の合成作品・画像専用rootを作成し、`failed_queue` テーブルを**ゼロ行**で作り、既存 `failed_records_fixture_guard.write_fixture_manifest()` と原DB SHAで固定。実行中のDBは生成テスト専用であり、元DB/WAL/SHM/元画像/ZIP/Cookie・設定、実Pixiv APIにアクセスしない。
- 既存 `tools/check_stage5_failed17_other_categories_gui.py` のowned process/nonce/loopback/WS・HTTP遮断/通常shutdown/lifecycle/前後SHAハーネスを新ケースにも再利用するため、`fixture_builder`・`initial_total` の選択と `generated_db_fingerprint()` を追加。NiceGUI側fixture fingerprintは自己申告ではなく生成済みSQLiteから実行時に抽出した行内容をSHA計算し、元Fletの空tupleのfingerprintと一致しなければ**GUI起動前に拒否**する。初期17件向け既存デフォルトの経路は維持。

## P3-408 元Flet本物GUI、2実観測

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'C:\Users\nat\miniconda3\envs\stenv-flet1\python.exe' -X utf8 -B tools/check_stage5_original_failed_empty_native.py artifacts/p3-408-real-flet-failed-empty-r1
# P408_FLET_EXIT=0
```

所有元FletアプリPID **133060**、実Desktop PixivVault HWND **50404184**。生成FakeDBは元失敗記録ゼロ、実 `失敗リスト` タブをクリック。初期表示は「失敗はありません」と「失敗した取得は0件です」、チップ「すべて 0」、個別行0。さらに検索欄へ `0009` を実入力しても、真0件の二行表示を保持し、既存記録が検索で消えただけの「一致する失敗はありません」へ変わらないことを確認。原失敗の再試行・削除・CSV等は押していない。

| Flet実Desktop原票 | SHA256 |
|---|---|
| ready `actual-original-full-gui-ready.json` | `83BCB5D6D4C2A5B58F06FD2964C09B645BDE3AA5AB078598F0F3CC9FCEF9943C` |
| guard `generated-failed-empty-source-guard.json` | `9D35F60396D728D6F713EF9B3C32556CB2A9405E329F8619F7774E28B74746D2` |
| `uia-true-empty.json` frame87 | `FC6F265033A5D087C511FE90514C1B7B4F7015B95084726C3665757F726FDA8E` |
| `uia-empty-search.json` frame89 | `DB552AB13E2491AD3274CF6EDB5805FBDF5FF58EF3C47C57F83EF8DF9E885E43` |

生成0件guardはreal original gui.py returned=true、source count33、source unchanged=true、元DB/Cookie未使用、formal_pass_added=0。実Flet通常exit0、関連PID0、元原本SHA不変。検索値そのものの完全なキーストローク原票は保存していないため、UIA文字列だけで送信入力を独立証明したとは呼ばない。

## P3-409 NiceGUI本物のFailedRecordsView、初回FAILと修正後GREEN

新 `tools/check_stage5_failed_empty_comparison_gui.py` は上の4原票を固定SHA/元HWND・PID/位相・実観測文言に結合し、以前P405の**17件中検索0件**の原Flet固定証拠と区別する。NiceGUI製品UIの `failed-count=0 / 0 件`、`failed-record=0`、empty二行文字列の実可視性、検索結果なし文言不在、頁1/1を確認する。生成SQLの実指紋は `4F53CDA18C2BAA0C0354BB5F9A3ECBE5ED12AB4D8E11BA873C2F11161202B945`。

新テスト `tests/test_stage5_failed_empty_comparison_gui.py` は元Flet UIA改変・偽owner・真空状態のDOM（誤ったno-match文、hidden空文、残存行）という負例を含む。既存17件SQLiteと同じfingerprint計算も比較して保持。局所は当初、P405関連と合わせて **19 passed / exit0**、再読込checker補修後の本対象は **11 passed in 18.70s / exit0**。

初回実GUI `artifacts/p3-409-nicegui-failed-true-empty-r1/result.json` SHA256 **`E6D036F704F6B829BF616DBFC9FBBDE2363461643D0B250B39184E845399D420`** は**success=false、2 steps、checker exit1**。初期真0件と検索中真0件は実際に通過したが、`page.reload()` 後は初期デフォルトタブへ戻るのに、checkerが `failed-records-view` を再度選択せず探したため、Locator not foundで失敗。**この旧FAILはそのまま保全し、製品GUI障害と断定せずcheckerの試験前提ミスとして切り分けた**。アプリ自体は正常終了exit0/forced=false、owned server子孫0、port解放、外部0、生成DB/source/harness不変。

修正は `open_failed_after_document_reload()` でブラウザ再読込後に実 `tab-failed` をクリック、`aria-current=page` と製品root可視性を確認してから真空状態を検証すること。別の生成DOMでは、タブ未選択時に行rootが無い/非表示であることを負例として、選択で戻る正例を追加。旧r1再実行や上書きはしていない。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed_empty_comparison_gui.py artifacts/p3-409-nicegui-failed-true-empty-r2
# P409_R2_GUI_EXIT=0
```

修正後r2 `result.json` SHA256 **`6E202C360D7C2B420C929823E8E5184C47CD614EF999786FC84DF38F88E7D2BA`**、success=true、3 steps（初期真0件、非一致検索でも真0件、ページ再読込・失敗タブ再選択で真0件復帰）、failure=null、formal_pass_added=0。通常exit0、forced_cleanup=false、owned server remaining0、port release=true、`lifecycle_verified=true`、外向きHTTP/WS0、JS/console errors0、生成DB/source/harness changed=false。checker SHA256 **`989890232AD2F76383313317643471E66761901540E13C406338FB10A3E15003`**、共通runner SHA256 **`5F4CAE5121966A065EA5D522B89288D8FB4D8ED8A7EFC75A9B7CF80D88FB7400`**。画像はNiceGUI CDP撮影で元Flet native GDIの同方式比較ではない。

## 独立監査・正式未達

worker-9へ本P408/P409と共通runner変更の最新コード・source・原票・r1/r2保存レシートをREAD-ONLY独立監査依頼済み。結果は回収まで未確定。元Q13-original-04のうち「真0件と検索一致なしを別表示」の同一生成条件・実製品GUIの**限定観測**であり、元152の全逐語expectedを正式合格へ昇格しない。正式 **0 PASS / 152 INCOMPLETE**、479原参照不変、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`。同native撮影・53px/全幅/異常時の保存復旧・実Cookie/専用Vault/配布/CI・全独立最終レビュー・本人受入は未達。push/main統合禁止。他担当dirty未操作。

## 既存17件fixture経路の非退行確認

今回の共通runner改変は新しい空DBの選択機能を追加したため、改変前のP396 r3を現runの成功へ自動転用しない。対象5 checker/testの局所関連回帰 **46 passed in 60.66s / P409_SHARED_LOCAL_EXIT=0**。既存のP396生成17件5カテゴリ+全件解除のGUIに対しても、最新共通runnerを使い、新rootで1回実操作した。

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_failed17_other_categories_gui.py artifacts/p3-396-nicegui-failed17-other-categories-r4
# P396_R4_SHARED_GUI_EXIT=0
```

r4 `result.json` SHA256 **`E135F63CD45B1CF92BC2438CBA8DD4106AACD00D550BE850BA871CC5197D1395`**、success=true/7steps、formal0、normalexit0/forced=false、observed server tree remaining0、port released、lifecycle verified、外部HTTP/WS/JS/console0、生成DB/source/harness不変。元のr2/r3、P409 r1/r2と別rootの履歴証拠として保全。

## 空DB改変負例・Flet launcher SHA拡張と最新r3

共有ハーネスのsource前後SHA監視に、P408本物Fletを起動した `tools/check_stage5_original_failed_empty_native.py` もtransitive対象として明示追加した（元source-copyの33ソース保護とは別のchecker provenance）。空生成DB作成後にテスト専用レコードを注入すると、実SQLiteから計算したfingerprintが元Flet空tupleと異なり、fixture manifest照合も拒否するという負例を追加。局所は **12 passed in 18.75s / exit0**。

変更後checkerは従来のr2成功を自動継承せず、`artifacts/p3-409-nicegui-failed-true-empty-r3` で専用NiceGUI実GUIを別起動。**success=true/3steps、checker exit0、formal_pass_added=0**、通常終了exit0、forced=false、owned server残存0、port解放、lifecycle true、外部HTTP/WS/JS/console0、生成DB/source/harness不変。結果SHA256 **`2656433E929EBEFA6802597DF574A5897E422A9E1CD233DF077F03F9C67FC5DA`**、最新checker SHA256 **`BB9A207CA8E70C01742CD305D1A2C50D76B90F60CCC3439388F049A184F43328`**。r1 FAILとr2 GREENを履歴として保持し、このr3を最新の新証拠とする。独立解消判定と正式受入とは区別する。

## 独立レビュー回収（worker-9）・安全設計レビュー（worker-8）

worker-9は本P408/P409 true-emptyと共通runnerの変更をREAD-ONLYで独立照合し、**LIMITED APPROVE・新規P1/P2なし**と最終報告した。レビュー時点にはr3とP396 r4の保存結果が存在し、最新checker SHA `BB9A207CA8E70C01742CD305D1A2C50D76B90F60CCC3439388F049A184F43328`、共有runner SHA `5F4CAE5121966A065EA5D522B89288D8FB4D8ED8A7EFC75A9B7CF80D88FB7400`、P408固定4原票のSHA全一致、元source-copyでの本物gui.main_window実構築、通常終了guard、生成0件と17件のDB実内容fingerprintの区別を確認した。

独立workerはP409 r1の`success=false / 2steps / failed-records-view not found`がtesterのreload後tab再選択漏れだったこと、r2/r3がそれぞれ独立した新rootで成功したことを区別。r3 result SHA `2656433E929EBEFA6802597DF574A5897E422A9E1CD233DF077F03F9C67FC5DA`とP396 r4 result SHA `E135F63CD45B1CF92BC2438CBA8DD4106AACD00D550BE850BA871CC5197D1395`を読み戻して照合。r3のアプリ通常exit0/強制終了なし、所有server子孫残0、port解放、lifecycle PID一致・services_closed/shutdown_requested true、外向き拒否0、生成DB/source/harness不変を確認した。workerによる追加GUI/pytest実行はしておらず、46PASS・12PASSは実装担当の実行票に基づく。OS全通信・全子孫・完全同nativeの証明ではない。

worker-8はQ13真0件の安全な経路をREAD-ONLY設計監査した。元Flet `builder_probe.FakeDB.get_failed_jobs()==[]` と保護された `launch_true_gui.run()`、NiceGUI fresh generated SQLite空`failed_queue`と通常`register_pages`→実`FailedRecordsView`は、模造UIでなく本物のGUIを使う安全な比較経路として成立。生成DBが0件の実count、未接続/未ロード/検索結果0件との文言区別、専用preferences/output/nonce、WebSocketとHTTPのloopback遮断、通常shutdown・原本未使用、録画不能なFlet Edit入力原票の限界を維持するよう確認。worker-8は実GUI受入試験を行っておらず、実測は前節P408/P409の原票と区別する。

**今回限定のchecker・GUIシナリオは独立LIMITED APPROVE。正式Q13逐語expected全条件を合格にしない。** 不足はDB未接続/読込失敗/中断・復旧、同native GDI撮影、元152逐語expected/479原参照、実Cookie・ZIP/Vault・Windows配布・CI・最終本人受入。正式 **0 PASS / 152 INCOMPLETE**、Stage5 **4 FAIL / 12 UNVERIFIED**、`release_ready=false`を維持し、main統合・pushは行わない。
