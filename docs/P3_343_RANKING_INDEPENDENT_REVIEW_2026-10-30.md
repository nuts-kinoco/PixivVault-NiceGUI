# P3-343 K03/K04 現source独立読取レビュー

**判定：REQUEST_CHANGES（P2 1件、K03/K04限定）**  
2026-10-30 JST。独立静的読取監査。今回のworkerは原Fletのソース・原チェックリスト・現ソース・既存試験と保存された受入記録のみ読んだ。実GUI、pytest、ビルド、実Pixiv API、ネットワーク、Git操作は実施していない。製品・試験・checkerは変更していない。

## 対象と同一性

| 対象 | 今回の読取対象SHA256 |
|---|---|
| `src/pixivvault_nicegui/ranking_source.py` | `41c69025db64859804a4c7589d40674a230799ed64ffa8fd5c3d5787fbd93555` |
| `src/pixivvault_nicegui/pixiv_read_client.py` | `87ae48dce6d0ba7c99e7fad89ff6e1a9477d83198e66ffad0a50043bb547153b` |
| `src/pixivvault_nicegui/ui/ranking_view.py` | `0bb228af6a3571b247bcc8b77c4deed553b5b0fa03e2dfe07f376c69bb317adb` |
| `src/pixivvault_nicegui/ui/personal_ranking_view.py` | `39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90` |
| `tests/test_ranking_control_autoload.py` | `ab766787014cf9aed74e861a740c7f33a953ffe481a78110c2711a7a4f2231ed` |
| `tests/test_ranking_novel_source.py` | `e2c154eaff0ec9da7d5db4edfefe0c4247cb58810eb8d662de8e9a97f20320cc` |
| `tests/test_ranking_novel_read_client.py` | `0dbf681e842a462cf6e4471de4a0d5ec314c9704530a251cccaa9bcac1b0908d` |

SHAは現ファイルを`Get-FileHash -Algorithm SHA256`で読み取り確認した値。製品4本は`artifacts/p3-343-ranking/edge-r3/result.json`の`source_before`/`source_after`（SHA256 `15849d8520847ce7b218bb2722f79d6dddba7d0fba117a3ebf04caef261bfaaf`）とも一致する。branch `phase11/p3-vault-cleanup-race-20261028`、受領した基準HEAD `2cf4a7a7286dd728e8df8125094498a3622babba` は引継ぎ指定値であり、本レビューではGitを再照合していない。

採用する原文は `S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md:97,98,104` のK-4「選んだ直後に一覧の中身が変わる」、K-5「選んだ直後にボタンの表示が選んだ日になり、一覧が入れ替わる」、K-11「サムネイル欄が空欄だけにならない」。原Flet `ranking_tab.py:51-55` に作品種別`novel`はなく、クリック先の小説本文readerは原K04の採用条件ではない。`docs/P3_343_RANKING_FORMAL_EVIDENCE_2026-10-30.md`の限定認定と同じ境界を採用し、正式152全体の認定へ広げない。

## P2：変更した検索条件と旧ページのカードを混在表示できる

**箇所**：`ui/ranking_view.py:55,66-73,177-216,217-228`。**静的再現手順**：

1. 公的ランキングでイラストAを正常取得して`self.page.query.content == 'illust'`、`gallery.state`にAのカードがある状態を作る。
2. 作品種別を`novel`へ変更する。`changed(refresh=True)`は`epoch`と通知だけを更新し、旧`self.page`/`gallery.state`を無効化・クリアしない（66–89行）。自動再取得の`fetch`が404または`RankingReadFailed`になるケースを考える。これは`ranking_source.py:219-224`で成立する正規の失敗経路で、認証情報・外部通信は再現に不要。
3. `load()`の例外処理はメッセージを変えるだけ（205–208行）、`finally`は`busy=False`と操作ボタンを戻すだけ（209–216行）。したがって、選択値は小説・旧カードはイラストA・`self.page.query`はイラストのまま残る。「取得できませんでした」だけではカードが**前の条件の結果**だと判別できない。ここで「並び順」を変更すると55行の`render()`が現在の`self.page.items`を再描画する（217–228行）。さらに「もっと見る」は旧ページを表示しながら、新queryとの差のため183行で何もせず戻る。

**期待**：原K-4/K-11の作品種別切替の結果と、失敗または未取得の状態とが混同されないこと。応答待ちやエラーでは旧結果であることを明示するか、その一覧を隠して再試行導線を保ち、新条件のソート・ページ追加操作が旧データを操作するように見せないこと。成功済みの別条件スナップショットを内部に残す設計自体は否定しない。

**影響**：K04で小説に切り替えた際にイラストサムネイルが残存し得る。誤った画面状態のまま並べ替えが反応し、追加ボタンは無応答になる。K03の日付切替にも同じ経路がある。正常取得・0件成功時の`render()`は新ページに差し替えるため、この指摘は**更新失敗・未取得と旧結果が重なる経路に限定**する。

**原版との差の注意**：原Flet `ranking_tab.py:1143-1157,1245-1247`でも取得失敗の瞬間に旧カードが残り得る。ただし原Flet `_current_items()`/`_render()`（877–894行）は**現在の条件のキャッシュキー**で旧データを判別し、条件不一致で再描画するとグリッドを空にする。新実装の`RankingView.render()`にはこの照合がなく、旧データを現在条件の下で再ソートするという追加の不一致がある。原版の失敗時挙動を全面的な新規機能要求へ読み替えない。

**既存証拠との関係**：`tests/test_ranking_control_autoload.py:63-87`は遅い古いGETの採用阻止を検査する。`tests/test_ranking_ui.py:53-74`はページ未取得段階のstale/closedを検査する。Edge-r3の`failure_distinguished_and_controls_reenabled`は失敗の区別と操作復帰を観測するが、「先にAを取得し、Bへ切替、Bの取得失敗後にソートする」組合せはnamed checksから立証できない。本件は**コードから成立する状態遷移の指摘**であり、今回実GUIで再現済みという意味ではない。

**修正後の確認条件**：旧ページと新queryが違う間の表示・ソート・もっと見るを一貫させ、K03日付・K04作品種別のGET失敗と404、失敗後再試行、正常応答、条件変更中の遅いGETの全てで、状態と画面の表示が一致することを確認する。既存の遅いGET直列化とclose/epochガードは維持する。

## ほかの焦点の読取結果

- **epoch／遅いreader／閉じた後**：`ui/ranking_view.py:66-103,177-193,229-252`は条件変更ごとにepochを更新し、同一viewのバックグラウンドGETを重ねず、古い応答やクローズ後の結果を破棄する。`open_unknown`はepoch・pin・open generationを全て確認する。`tests/test_ranking_control_autoload.py:63-114`と`tests/test_ranking_ui.py:53-115`にも対応検査がある。ここに確認済みの新規P1/P2は認めない。
- **K03カレンダー**：`ui/ranking_view.py:104-172`は日付の上下限と正規形式、ダイアログnonce/epoch、キャンセルおよび選択直後の再取得をガードする。保存済みEdge-r3は両viewportで日付入力・カレンダー選択・取消・未来日付のnamed checksに成功している。実ネットワークの全日付・元の未定義開始条件まで立証するものではない。
- **novel専用APIとnamespace**：`ranking_source.py:92-147,164-224`は`novel:<id>`を持つ独立`RemoteBookmark`、専用`get_novel_ranking`と表紙画像を使用し、タグ/R18の未観測経路を明示拒否する。`pixiv_read_client.py:293-345,1255-1311`は`ranking.php`のartwork許可リストを維持し、別`/ajax/ranking/novel`をHTTPS・host・順序付きquery・mode・page・dateで制限する。`remote_media.py:94-114,209-247`はnovelをイラスト詳細APIへ送らず、表紙から画像化する。合成Edge-r3の`novel_namespace_and_cover_decode`と`novel_append_and_end_of_results`はこの限定配線を支持する。原K04に小説本文readerや追加のクリック遷移は必要としない。
- **novel parser／R18・AI・sortの正直な表示**：`ranking_source.py:100-147`はcanonical string ID、rank/bookmark count、タグ、CDN表紙、既知マスク、AI未知値を厳格に扱う。`ranking_source.py:168-169`は未確認のnovel R18・タグを取得前に拒否し、`RankingPage.order_label`（211–215行）はnovelの閲覧数・伸びしろが未取得と明記する。novel `ai_type='2'`を除外対象とするのは現時点の**観測値ベース**（139–144,202–209行）であり、フィールド意味の全Pixivモード独立検証は本レビューではしていない。`view_count`/`previous_rank`がnovelでは`None`のため、それらのソート操作が新しい情報で並び替えた証拠にはならない。これらは未立証範囲として扱い、意味が確定していないという理由だけでP2と断定しない。
- **personal継承**：`ui/personal_ranking_view.py:10-18`は親のlifetime属性を明示初期化する。`load()`（175–206行）はepochと取得可能性を採用前に再確認し、`_refresh_context`（113–127行）はクローズ・非active・世代変更の遅い更新を捨てる。`set_active`（225–231行）は親を通す。旧検索条件での表示残留には`_show_unfetched`（73–86,150–160行）を使っており、上記公的ランキングの問題と同一と判断しない。今回の静的範囲では個人ランキングに新たなP1/P2は立証されない。

## 受入・判定の限界

`artifacts/p3-342-monitor/full-r2/run.json:1-18`はJunit 4162件中 **4156 PASS／0 FAIL／0 ERROR／6 SKIP**、source576ファイル不変・exit0。`artifacts/p3-343-ranking/edge-r3/result.json:177-340,513-515`は合成Edge 1280×720・1920×1080の公的14項目/個人15項目、外部通信0、source170ファイル不変、正常終了/port解放を記録する。これらは既存実施者の保存結果であり、今回のworkerが再実行した結果ではない。

**REQUEST_CHANGES は公的K03/K04の旧条件表示と新条件表示の一致を対象とする限定判定**。現状の合成成功、原K04の限定描画成功、K09–K12の既存観測を取り消すものではなく、正式152全体の進捗・完成認定を変更しない。上記P2の修正・必要な再検証後に、この4本のsource SHAを更新して独立再レビューすること。
