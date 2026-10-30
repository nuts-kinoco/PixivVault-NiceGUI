# P3-345 公開ランキング wire 数値契約・独立読取レビュー

**判定：限定 APPROVE（本修復範囲の新規 P1/P2 指摘なし）**  
2026-10-30 JST／worker-11。対象は実Cookie読み取りにより判明した `ranking.php` 公開ランキング行の **`illust_type`／`illust_page_count` の型不一致**への限定修復。今回の独立監査はソース・テスト・保存済みの型/件数だけの診断と試験証拠を読取照合した。新たな試験、実Cookie/API、GUI、画像、ネットワーク、Git、Claude操作は行っていない。変更は本docの新規作成のみ。

## 同一性と根拠

| 対象 | SHA256・証拠 |
|---|---|
| `src/pixivvault_nicegui/ranking_source.py`（現行） | `bb7e6641b03a3da7da8aa5d04465c0423c60be02749ee3cb38b4b93954518f29`、read-only `Get-FileHash` と直接ソース読取 |
| `tests/test_ranking_public_wire_numbers.py`（新規31ケース） | `911f3daa631cb0b92c83689c143c45af297781cecf8e2f690c9da017127be1a1`、read-only `Get-FileHash` と全文読取 |
| `src/pixivvault_nicegui/personal_ranking.py` | `aa946746d6f90a691bcce2d955b9ac90698f30cd56fc69fd4e146d87892aad13`、read-only `Get-FileHash` |
| 実GET型証拠 | `artifacts/p3-344-real-read/schema-r1/result.json:32-81`。認証検証成功、50行、旧パーサ正常解釈0行・`invalid numeric field` 50回、`illust_type`／`illust_page_count` がそれぞれ全50行で `str`、`rank`／`yes_rank`／`rating_count`／`view_count` は全50行で `int`。画像・応答ペイロード保存0、識別子公開0、書込み0、client close 確認済み。 |
| RED / GREEN | `artifacts/p3-344-real-read/wire-red-r1/{result.xml,console.log}` **31件中5 FAIL/26 PASS**、`wire-impact-r1/{result.xml,console.log,exit.txt}` **332 PASS/0 FAIL/0 ERROR/0 SKIP、exit 0**。今回のworkerの再実行結果ではない。 |
| 修復後の実GET受入 | `artifacts/p3-344-real-read/read-r2/result.json`（SHA256 `621a553b4af08e2fbffc9589e93fbd9e19a0765a803e7f3e75185dd7e3173975`）。`passed=true`、`production-acceptance`、**GET 5件のみ**、公開イラスト50件・小説50件、実サムネイルRAMデコード480×742、Cookie不変、client close確認、画像/実payload/識別子保存0・書込0・エラー0。保存 `source_before` の `ranking_source.py` SHA は `bb7e6641b03a3da7da8aa5d04465c0423c60be02749ee3cb38b4b93954518f29` で現行ソースの読取専用SHAと一致、`source_changed=false`。 |

旧版 RED は `tests/test_ranking_public_wire_numbers.py:12-28,43-49` に対し `illust_type='0'/'1'/'2'` が旧 `_number` により拒否され、合成50行の `RankingSource.fetch` も失敗、重複ページ数一致ケースも失敗した。RED `console.log:1-61` は旧 `ranking_source.py:77` の数値型拒否を明記。実GETの型証拠はこの失敗機構と整合する。

## 変更された wire 境界：file:line の独立判断

1. **公開の2フィールドだけ**：`ranking_source.py:57-74,80-113`。新規 `_public_wire_number(value, minimum, maximum)` は `type(value) is str` のときだけ `1<=len<=4`、ASCII、10進数字、複数桁の先頭0なしを検証してから `int` 化する。整数は元の `type(value) is int` を維持し、最後に範囲を必ず検証。bool/float/Unicode数字/符号/空白/小数表記/空文字/leading zero/上限超過、`None` は拒否。公開作品種別は `0..2`、公開ページ数は `1..1000` に制限される。例：`'0'`（type）、`'1000'`（ページ数）は受理、`'00'`、`'+1'`、`'１'`、`True` は拒否。
2. **ページ数の実数反映と別名衝突**：`ranking_source.py:89-107`。`illust_page_count` が存在する公開行では正規化したページ数で `RemoteBookmark.page_count` を設定し、`page_count_known=True` にする。`page_count` が同時に存在する場合は **厳密に `int` かつ値一致**を要求する。None/str/bool や値不一致は拒否し、曖昧な優先順位を作らない。両フィールドが存在しなければ従来どおり初期1件・`page_count_known=False`。テスト `test_ranking_public_wire_numbers.py:12-19,22-28,37-49` の宣言件数・合成50行・相違拒否・unknown保持と整合。
3. **ビューアへの接続**：`bookmark_remote_adapter.py:51-78` は `RemoteBookmark` の既知フラグ・件数を `Artwork` へ伝播。`remote_media.py:83-114` は既知件数で metadata 再取得を不要とし、`remote_media.py:150-174,209-230` は remote ページ集合の長さを既知値と照合する。従って宣言値が不正な場合に閲覧できるページを捏造することはなく、実取得の整合は従来ガードで再確認される。今回の332件の保存試験に **独立した remote_media 専用テストクラスは含まれない**。ここは静的読取判断に留める。
4. **その他数値／search／novel／personalは拡張せず**：`ranking_source.py:57-60,97-113` では `rank`／`yes_rank`／`rating_count`／`view_count`／`ai_type` と `x_restrict` は旧 `_number` の整数専用経路を維持（`test_ranking_public_wire_numbers.py:52-55`）。`query.tag` の検索は `illust_page_count` 用変換分岐を通らず、作品種別も `_number` のまま（`ranking_source.py:83,89-103`）。`parse_novel_item` は別関数 `ranking_source.py:116-171` で独自の厳格schemaを維持し、`RankingSource.fetch:190-220` でも明示分岐。`personal_ranking.py:225-239` は個人検索行を整数型作品種別に正規化したうえで `RankingQuery(tag='personal')` に渡すため、新しい公開限定変換に入らない。`pixiv_read_client.py:1255-1267` の公開GETはペイロードの `contents` をそのまま返す従来経路で、URL制約・HTTPクライアントを修正していない。
5. **試験の実際の範囲**：`wire-impact-r1/result.xml` の332件は、wire新規31、ランキング source16/UI5/condition8/transport10、novel source25/read-client33、personal source5/UI3、pixiv read-client187/lifecycle9 の12クラスに一致する。新規31件は公開作品種別3件、合成50行1件、不正作品種別12件、不正ページ数13件、別名整合1件、その他数値非許容1件。`wire-red-r1` は **5 FAIL/26 PASS**、`wire-impact-r1` は **332 PASS**。この試験群とは別に、修復後の実GET受入成功は上記 `read-r2/result.json` で確認済み。

## 限界と P1/P2 判断

- `schema-r1` は保存済みの型・件数診断であり、値・存在キーの原配列や実ID・タイトル・画像・URLパスを持たない。`page_count` の `NoneType` 集計だけでは **キー自体が無い**ケースと**明示的な null** を区別できない。現コードは前者を許容して `illust_page_count` を採用し、後者で重複aliasに遭遇すれば安全側に拒否する。修復後の実GET受入は `read-r2/result.json` の `passed=true`・公開イラスト50件で確認済み。ただし同結果にも実payloadは保存されず、元の `page_count` キー有無自体は未特定。
- `ai_type` の実GET診断が全50行 `NoneType` でも、その値を安易に `0` や非AIと解釈しない。`ranking_source.py:226-233` の exclude-AI時の既存追加検証と失敗条件は維持される。
- テスト済みの合成schemaと、実環境の全モード・全日付・全ユーザーのschemaが一致するとは認定しない。`read-r2` では実GETとサムネイルのRAMデコードが成功しているが、全モード・全日付の実API互換、GUIでの実画像表示、native、性能、正式152の判定をこのレビューから拡張しない。

**本スコープの判定は限定 APPROVE**。保存済み実GETにおける50件全拒否の原因とソース修正が対応し、型の受容範囲は公開2項目に閉じ、別名不一致は拒否され、合成50行／31ケースと332件の回帰を通過。現ソースから再現可能な新規 **P1/P2は立証されない**。修復後実GETの公開イラスト50件・小説50件・サムネイルRAMデコード成功は `read-r2` により追認し、`page_count` キー有無の元payload照合は未実施のままとして、原152全体・配布・性能の完了承認は行わない。

