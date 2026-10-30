# P3-343 ランキング K01–K14 原152期待と現Edge実操作の証拠結合

**2026-10-30 JST／読み取り専用照合／正式152総計は変更しない**

## 1. 判定の意味・原本

本記録が認定するのは、**合成readerを使った現製品Edge実操作の限定範囲**だけ。
「操作限定PASS」と「原152の正式ID全条件PASS」は異なる。ここで新規に正式全条件を認定したIDは
**0件**。公式歴史集計 **33完了／43未達／76未確認** とv8の現候補正式再認定 **0/152** は維持する。
新しい本文reader、余分な外部通信、追加のノベル保存操作等をK04の要件として作らない。
本記録は**筆者自身が行ったbackendの独立承認ではない**。
K04専用backendの以前の局所CPU成功・prime/別担当の実装判断と、
今回の合成GUI受入は別の証拠である。

| 参照原本 | SHA256・位置 | 用途 |
|---|---|---|
| `docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md` | `78fa125f87a82e241a983d4be7d90c5b49c4e1f702203e8b9500a47723c4d1b6`、行39–52 | K01–K14正式ID・操作名・原条件索引。K04はSW K-4/K-11「小説表示も含む」 |
| `docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json` | `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4` | 各recordの`expected_full`、`original_expected_references_318`、`start_condition`、旧部分proof |
| `S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md` | `065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28`、行90–107 | SW K-1〜K-14原literal。原版の2026-09-03観点表（ファイル更新2026-09-04）の実操作・期待 |
| `artifacts/p3-343-ranking/edge-r3/result.json` | **`15849d8520847ce7b218bb2722f79d6dddba7d0fba117a3ebf04caef261bfaaf`** | 現製品合成Edgeの実行・観測・before/after source SHA・終了 |

v8は**K01–K14全件の完全な開始状態を明示していない**：
`start_condition=null`、`start_condition_provenance_v8.status=not-explicitly-defined`。
今回の証拠から後付けして開始条件の正本を書き換えない。
Edge-r3で実際に使われた条件（合成reader、2 viewport、UIの当該control）を
「証拠の実行条件」として下記に分けて記す。
v8の「expected_full」は原参照の転記であり全spec網羅の確約ではない。

## 2. 今回のEdge観測とsource拘束

`edge-r3/result.json:1-5,177-340,341-515` の保存結果による。

- **開始**：2026-10-30 13:26:25 JST、合成readerを接続した現製品。
  **実Pixiv GET/POSTではない**。外部リクエスト0、console/page/error各0。
- **世間ranking**：1280×720と1920×1080、それぞれ14 named checks、
  `public[viewport].passed=true`。原票が列挙する対象は
  K03/K04/K05/K06/K07/K08/K14（**全K群合格の意味ではない**）。
  両解像度とも`horizontal_overflow=false`。
- **個人ranking**：1280×720と1920×1080、それぞれ15 named assertions。
  `tag-add`、`search-five`、`include-exclude`、`date-preset`、
  `per-author-limit`、`bookmark-requery-only`、
  `sort-requery-only`、`zero-results`、`clear`、`filter-save`、
  `tab-return`、`reload-persisted`、`failure-distinct`、
  `disabled-empty-tag`、`candidate-toggle-default-open`。
  横スクロール幅はそれぞれ1280/1920、panel幅1248/1872。
- **終了**：正常終了確認`shutdown_acknowledged=true`、
  server`exit_code=0`、`living=[]`、子0、port解放、
  `services_closed=true`、強制cleanupなし、`denied_external_connections=0`。
  edge-r3自体は`success=true`。
- **source binding**：保存原票の`source_before`と`source_after`の
  **170ファイルが全件一致**、`source_changed=false`、
  `checker_unchanged=true`、checker SHA
  `f2968277e0b7ec10092fa520096e8c593f3f6ab3f4844b00ffd14a5f1ccc1db1`。
  さらに現worktreeで同170個のSHAを読み取り再照合し **170/170一致**。
  これはedge-r3の製品source拘束であり、実ネットワークの意味的互換や将来freezeを保証しない。

特に関連する現製品SHAは
`ui/ranking_view.py=0bb228af6a3571b247bcc8b77c4deed553b5b0fa03e2dfe07f376c69bb317adb`、
`ranking_source.py=41c69025db64859804a4c7589d40674a230799ed64ffa8fd5c3d5787fbd93555`、
`pixiv_read_client.py=87ae48dce6d0ba7c99e7fad89ff6e1a9477d83198e66ffad0a50043bb547153b`、
`ui/personal_ranking_view.py=39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90`、
`personal_ranking.py=aa946746d6f90a691bcce2d955b9ac90698f30cd56fc69fd4e146d87892aad13`。

## 3. 中心ID：逐語期待・開始条件・実操作・観測・判定

以下の「原literal」はv8と原SWの同一文言（補助索引との合致を確認）。
全行の開始条件は上記の**原文未定義**。
「限定可」は証明された合成GUI操作だけに付与し、**正式全ID合格とはしない**。

### K03 — ランキング日付を指定する

- **原literal**（SW K-5、原SW行98、v8 K03）：
  「選んだ直後にボタンの表示が選んだ日になり、一覧が入れ替わる」。
  原index行41には追加で「未来日付失敗も含む」。
- **実操作/観測**（Edge-r3 public両viewport）：
  `date_input_applies_without_fetch_click`、
  `calendar_day_click_updates_list`、
  `calendar_cancel_does_not_fetch`、
  `future_date_no_request_preserves_previous_results` がPASS。
  現`ui/ranking_view.py:47-50,59-60,90-103,113-117,156-176`は、
  日付選択→日付表示更新→世代付き検索へ接続。不要な再取得を行わない取消も確認。
- **判定**：日付選択直後の表示/合成一覧更新は**限定認定可能**。
  「未来日付を受け付けず前結果を保持」は観測済みだが、
  **未来日付時の画面上の失敗文言そのものは今回のnamed checkから一意に確定できない**。
  実GETにおける指定日結果と全原開始条件は未認定。追加通信を新たな必須条件にはしない。

### K04 — 作品種別でランキングを絞る

- **原literal 1**（SW K-4、原SW行97、v8 K04）：
  「選んだ直後に一覧の中身が変わる」。
- **原literal 2**（SW K-11、原SW行104、v8 K04）：
  「サムネイル欄が空欄だけにならない」。
  同じ原行の操作は「小説のみのランキング（作品種別＝小説）を見る」。
  原index行42の「小説表示も含む」を削らない。
- **実操作/観測**（Edge-r3 public両viewport）：
  `novel_namespace_and_cover_decode`、`novel_append_and_end_of_results`、
  `novel_ai_filter_no_illustration_detail`、`novel_bookmark_sort`、
  `unverified_novel_r18_explicitly_unavailable_no_remote_read` がPASS。
  小説とイラストのID空間を分離した合成結果が表示され、
  小説のcoverが画像としてdecodeされる限定経路を確認。
  現`ui/ranking_view.py:46-60,66-103,177-204,217-227`では
  content変更時に自動検索し、次の状態だけ採用してカード画像へ結線する。
  `ranking_source.py`・`remote_media.py`の現SHAはr3と一致。
- **判定**：**一覧切替の現製品配線と、非空の小説表紙を表示する合成GUI結果に限り認定可能**。
  原literalにない小説本文readerや余分な外部通信を要求しない。
  ただし`novel_namespace_and_cover_decode`は具体的な描画成功を示すにとどまり、
  実Pixivのあらゆる小説cover、全モード・全日付、masked行を含む全原受入の証明ではない。
  今回のwriterはbackend独立承認を行っていない。

### K09 — 個人ランキングの含む/除外タグを編集して検索する

- **原literal（SW K-9、原SW行102）**：「すべて反応する」。
  原操作は「自分モードの部品を1回ずつ（タグ追加・含む／除外タグ・ブックマーク数プリセット・並びプリセット・期間プリセット・作者あたり件数・候補トグル）」。
- **v8の残る原literal参照**：K09にはこのSWに加え
  **RC #60A行135–141の7期待**が紐付いている。具体的には
  「おすすめタブのカードを右クリック→メニューが出る（左クリックの pixiv 遷移は従来どおり）」、
  「中クリックで空のメニューが開かない」、
  Pixivブックマーク時のスナックバー、非表示の再読込、取消/元に戻すの連打、
  boost作者の節4更新、DLキュー入口である。
  **今回のpersonal 15操作はこれら別画面のRC #60Aを実施していない**。
  iPhone連携など別途除外された範囲を新規K09要件に復活させない。
- **実操作/観測**：両viewportで
  `candidate-toggle-default-open`、`tag-add`、
  `search-five`、`include-exclude`、
  `filter-save`、`tab-return`、`reload-persisted`、
  `zero-results`、`failure-distinct`、`disabled-empty-tag` を観測。
  合成候補を追加し含む/除外タグの状態と結果・保存復帰を確認。
- **判定**：**SW K-9の個人検索・操作部品の限定合成GUI範囲は認定可能**。
  ただしv8 K09全参照の完了とは呼べない。RC #60Aの個別原期待と、
  実借用アカウント/実Pixivの検索結果は本r3では立証していない。

### K10 — ブクマ数条件を変更する

- **原literal**（SW K-9、行102、v8 K10）：「すべて反応する」。
- **実操作/観測**：両viewportで`bookmark-requery-only`、
  `search-five`、`sort-requery-only`、`filter-save`、`reload-persisted`。
  ブックマーク数変更はローカル条件変更だけで結果を偽装せず、
  明示の再検索時に合成readerへ新条件を渡す。
- **判定**：**条件選択→明示再取得の合成GUI限定は認定可能**。
  原SWには全プリセットを実Pixivで再取得することを必須とする逐語条件はない。
  ただし全選択肢の境界・原初期値・借用検索先の実値は未立証。

### K11 — 個人ランキングの期間条件を変更する

- **原literal**（SW K-9、行102、v8 K11）：「すべて反応する」。
- **実操作/観測**：両viewportで`date-preset`、
  `include-exclude`、`zero-results`、`clear`、
  `filter-save`、`tab-return`、`reload-persisted`。
  取得済み合成集合に期間条件を適用し復帰する一連操作を確認。
- **判定**：**期間選択/ローカル反映の合成GUI限定は認定可能**。
  原初期値・全期間プリセットの境界は今回のnamed assertionsだけでは網羅不明。

### K12 — 作者あたり件数上限を変更する

- **原literal**（SW K-9、行102、v8 K12）：「すべて反応する」。
- **実操作/観測**：両viewportで`per-author-limit`、
  `include-exclude`、`zero-results`、`clear`、
  `filter-save`、`reload-persisted`。
- **判定**：**上限変更/合成結果絞込/保存復帰の限定GUIは認定可能**。
  0（無制限）や各境界値、実検索元の作者分布は別の証拠がなければ全条件認定しない。

## 4. その他 K ID の対応（限定範囲を保持）

| 正式ID | 原literalの出典/中心期待 | edge-r3で確認できた操作/観測 | 今回の認定・不足 |
|---|---|---|---|
| K01 | SW K-1/K-8：全操作部品反応、自分モードへの見た目と選択表示の移行 | 公的・個人の両ビューを別々に実操作、`tab-return`は観測 | モード相互切替時の選択表示はnamed checkで独立明示なし。**部分のみ** |
| K02 | SW K-1/RC #46：期間・R18不可理由・タグ・AI等 | `period_and_supported_r18_auto_reload`、`novel_ai_filter_no_illustration_detail` | 全mode/RC各軸の結果は未網羅。**部分のみ** |
| K05 | SW K-6：「切り替えた直後に件数か中身が変わる（変わらないなら、変わらない理由が画面で分かる）」 | `period_and_supported_r18_auto_reload`、`unverified_novel_r18_explicitly_unavailable_no_remote_read` | 対応modeと小説未対応理由の**合成GUI限定可**。全mode実Pixiv結果は不明 |
| K06 | SW K-6：K05と同じ原literal | `novel_ai_filter_no_illustration_detail` と条件変更の自動再取得 | 合成小説AI結果・非画像詳細参照防止の**限定可**。全mode/未知値の実読は別 |
| K07 | SW K-7/K-10：「確定直後に絞り込まれる」「『0件』と分かる文が出る。前の結果が残ったままにならない」 | `tag_submit_only_and_zero_results`、`failure_distinguished_and_controls_reenabled` | Enter確定/0件/失敗区別の**合成限定可**、実Pixivタグ結果ではない |
| K08 | SW K-3：「選んだ直後に並びが変わる」 | `novel_bookmark_sort`、publicでのソート挙動 | 小説ブクマ順の**合成限定可**、全ソート軸は不明 |
| K13 | SW K-1：操作部品が応答する。原indexは明示更新・失敗復旧も索引 | `explicit_fetch`、`failure_distinguished_and_controls_reenabled` | 成否の視覚応答は**合成限定可**、実GET失敗の復元は未立証 |
| K14 | SW K-1/RC #59：グリッド末尾の「もっと見る」、手動追加、読み込み中/終端、複数頁後タブが固まらない | `novel_append_and_end_of_results`、`tab_return_retains_snapshot_without_request` | 追加/終端は**合成限定可**。最終セル到達・重い複数頁・失敗復帰/性能の全RC期待は不足 |

## 5. 正式全ID認定に上げない根拠と追加作業の境界

**正式152各IDの全条件認定は、この1本では一件も更新しない。**
その理由は抽象的な「テスト不足」ではなく次のように分かれる。

1. **K03**：SW K-5の即時更新は現製品合成GUIで覆えたが、
   indexが追加する未来日付の画面上エラーテキストと、原未定義の完全初期条件は別。
2. **K04**：SW K-4/K-11の一覧切替・小説cover decodeは合成実操作で確認済み。
   実Pixivの表示幅/未閲覧cover全般を検証したと主張せず、逆に原literalにない
   小説本文readerや追加APIを受入条件として持ち込まない。
   小説クリック先の仕様論争は**K04原literalの合否に混ぜない**。
3. **K09**：SW K-9の個人filter操作は受入済みの範囲が明瞭。
   v8が連結するRC #60Aの他画面操作（bookmark/非表示/boost等）は
   本r3では未実施。原参照を勝手に削らず、実施済みと偽らない。
   利用者から除外指示のあるiPhone連携は再採用しない。
4. **K10–K12**：SW K-9の選択/再検索/ローカルfilter反映・保存復帰は
   2 viewportの合成GUIで確認できた。
   原文未定義の初期fixture・全プリセット数値・実借用検索を
   今回の合格から推測しない。
5. **横断範囲**：実Pixiv GET、Windows native配布、原Flet同条件比較、
   CI/独立backendレビューなどは別の承認・受入ゲート。
   「K04の一覧/サムネ条件を認定するため」に新しい通信や
   読者画面の追加を要求するものではない。古いv8は今回更新しない。

この文書だけを新規作成。製品・tests・tools・旧docs・Git・GUI・pytest・ネットワーク・
Claude・元DB/媒体/Cookieは操作していない。既存のedge-r3観測結果を加工せず照合した。
