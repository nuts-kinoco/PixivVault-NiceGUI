# P3-344 ranking K03/K04 P2 修復の独立読取レビュー

**判定：限定 APPROVE（前回 REQUEST_CHANGES の P2 解消）**  
2026-10-30 JST／worker-11。現在の `ui/ranking_view.py` と継承先、既存テスト・Edge 保存証跡を独立して静的照合。今回の worker は pytest、Edge、実API、Git、ビルド、新checkerを実行していない。製品・テスト・checker・前回レビューは変更していない。この文書の追加のみが変更対象。

## 基準と適用範囲

- 前回判断：`docs/P3_343_RANKING_INDEPENDENT_REVIEW_2026-10-30.md:22-38,52`。旧条件で成功取得したカードが新条件の取得失敗後も残り、並び順だけ旧ページを再描画し、「もっと見る」は無応答になる P2 を `REQUEST_CHANGES` とした。前回 `ui/ranking_view.py` SHA256 `0bb228af6a3571b247bcc8b77c4deed553b5b0fa03e2dfe07f376c69bb317adb`。
- 今回対象：`src/pixivvault_nicegui/ui/ranking_view.py`。保存済み `artifacts/p3-343-ranking/edge-r5/result.json` に記録された `source_before`/`source_after` の SHA256 は `d8d40fc1648eb646c7cab5ed0e4f8a627c858c609befabdc113e0b803883fd4b`、両者一致・`source_changed=false`。現在の本文は以下の行を読取照合した。現workerは改めて `Get-FileHash` を実行していない。
- 影響追跡：`src/pixivvault_nicegui/ui/personal_ranking_view.py`（Edge-r5保存SHA `39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90`）、`tests/test_ranking_failed_conditions.py`、`tests/test_ranking_control_autoload.py`、`tests/test_ranking_ui.py`、`artifacts/p3-343-ranking/acceptance.py`。
- `ranking_source.py` と `pixiv_read_client.py` は今回の修正対象に含めない。原 K03/K04 の日付・作品種別・サムネイルの限定受入を評価し、小説本文 reader 等の追加仕様を設けない。

## 指摘 P2 の再現経路と修正照合

| 焦点 | 現source の具体的根拠と結論 |
|---|---|
| 旧 snapshot と新 query の識別 | `ranking_view.py:174-181` は `self.page.query == self.query()` を毎回判定し、日付不正時も不一致と扱う。`changed():66-73,86-90` は epoch 更新・最新条件の再取得要求と操作状態更新を行う。旧 `page`・pin・カードを保持しても、新条件の取得済み結果として扱わない。 |
| 失敗・未取得中の append と sort | `ranking_view.py:182-187` は閉鎖・busy・条件不一致のとき `more_button` と `sort` を無効化し、`load():197` も不一致ページ追加を直前に拒否する。タグ未送信時も `changed(refresh=False)` → 同じチェックを通る。`render():233-246` は現在 query に合致しない旧 `page` を再描画せず、故意に発火させた sort event でも `GalleryState` に触れない。旧P2の「見かけ上ソートできる／moreが応答しない」はこの経路で解消。 |
| 失敗時の文言と保持 | `ranking_view.py:188-189,219-232` は前ページがある失敗に `前の一覧を保持しています。` を付し、`finally` で busy を解除後、条件一致だけに操作を復旧する。失敗時は `page`/pin を上書きせず、再試行できる fetch を戻す。日付不正は `193-196` で同様に旧一覧の注記を付ける。 |
| 成功時の復帰 | `ranking_view.py:205-218,225-246` で新条件の成功応答を `self.page`/provider に採用して `render` し、旧 pin を閉じ、`finally` で新条件に一致した append/sort を回復する。成功した空結果も新ページを採用する。 |
| stale GET と close | `ranking_view.py:91-104,190-207,247-270` は重複GETを直列化し、epoch/active/closed の不一致を棄却。`test_ranking_control_autoload.py:63-114`、`test_ranking_ui.py:53-115` に対応する保存試験結果がある。 |

**再現シナリオ**：日間イラストAを取得後、週間Bを選択し B の GET を404/500で失敗させる。元 `page.query.mode=daily`、現在 `mode=weekly`、カードAとpinは保持しつつ more/sort は disabled、通知は旧一覧だと示す。プログラムから sort の値を変えても `GalleryState` とカード identity は不変。`load(append=True)` は通信せず、新条件の手動再取得に成功すると B の `page` に差し替え、旧pin閉鎖・sort/more復旧となる。これらは `tests/test_ranking_failed_conditions.py:13-48` の明示的 assertions、および `acceptance.py:271-289` の GUI 観測条件に対応する。タグ未送信時の同類経路は `test_ranking_failed_conditions.py:51-74`。

## 継承・回帰範囲

`PersonalRankingView` は親コンストラクタを使わず lifetime 属性を個別初期化する（`personal_ranking_view.py:9-18`）。親に今回追加された `_sync_result_controls`／`_result_matches_conditions` は親の `changed/load/render` から呼ばれ、個人ビューはこれらを独自実装する（`150-160,175-224`）。個人側は `_server_key()/_current_page()`（`60-65`）と `_show_unfetched()`（`73-86`）、`availability()`（`128-149`）で server 条件不一致と append 可否を処理し、`set_active` は親を経由（`225-231`）。共用 `_RankingGallery.more` は subclass の `load` に委譲され、`load:176-177` の条件不一致ガードを通る。現在の読取範囲で、この修復起因の新しい個人ランキング P1/P2 は立証できない。

## 保存済み受入証拠と限界

- **RED → GREEN**：`artifacts/p3-343-ranking/condition-red-r1/result.xml:1-18` は 3 TEST / 3 FAIL（404/500 の more enabled と未送信タグ時 more enabled）。`condition-impact-r1/result.xml:1` は修正後 **79 TEST / 0 FAIL / 0 ERROR / 0 SKIP**。`test_ranking_failed_conditions.py:28-45,62-71` の旧カード・pin継続、無効化、stale sort 非再描画、再取得復帰 assertions を含む。
- **Edge-r5**：`artifacts/p3-343-ranking/edge-r5/result.json` は2026-10-30 13:49:53–13:50:24 JST、`success=true`。公的rankingは **17 named checks × 1280×720/1920×1080**、個人は **15 assertions ×2**。新条件日付 20261003 が失敗しても旧 20261002 の card IDs `c157,c163,c169` を維持、通知 `取得できませんでした。再試行してください。 前の一覧を保持しています。`、append/sort disabled、再び20261002を取得して両操作復旧。novel 追加取得の一度失敗→再試行→末尾まで、個人 K09–K12 の操作も保存されている。`acceptance.py:271-341` の検査方法と一致。
- 同結果には console/page/errors 0、external requests 0、`source_changed=false`、`checker_unchanged=true`、正常 shutdown 承認、server exit 0、living 0、port released、services closed、強制cleanupなし、denied external 0 が記録されている。Edge-r5 の入力は **合成 reader/Vault**（`acceptance.py:1-6,120-144`）。今回の worker は結果原票を読み取ったのであって実操作を再実行したわけではない。
- **残る小さな表現上の留保**：新条件の自動GET待ちでは旧カードを残して `notice='取得中…'` と表示する（`ranking_view.py:66-73,190-203`）。この表示だけには「前の一覧」の逐語はない。条件変更通知・取得中表示・旧結果での more/sort disabled があり、失敗確定時の旧一覧明示は実測済みのため、旧P2を再び成立させる根拠とは認定しない。将来、取得待ち中にも「前の一覧」と明示する UX 改善は検討可能。ここをK03/K04の新しい必須操作条件へ広げない。
- **full344-r1**：読取時点（2026-10-30 13時台）、`artifacts/p3-344-native/full-r1/runner.json` は 13:52 JST に開始、`console.log` は39%付近までの進行表示、`result.xml`・終了集計は未確認。実行中テストには触れず、全suite GREEN や Windows配布完了をこのレビューから主張しない。

## 限定結論

前回 `REQUEST_CHANGES` の **「旧条件カードを新条件のものとして more/sort 操作させる」P2** は、静的ガードと保存済み RED/GREEN・実Edge 両画面幅の対応観測により **解消を確認**。対象の現修復に追加の **P1/P2 は立証されないため、K03/K04 の当該P2修復だけを APPROVE** する。これは既存の前提付き合成GUI受入の範囲である。

**PixivVault全体のFlet→NiceGUI完全移植・正式152操作・実API全条件・性能・配布の完了判定ではない**。`docs/P3_343_RANKING_FORMAL_EVIDENCE_2026-10-30.md:5-13` の公式歴史集計 **33完了/43未達/76未確認**、v8現候補の正式再認定 **0/152** を変更しない。
