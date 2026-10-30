# PixivVault 315作業ツリー：314引継ぎ以降の採用機能・限定証拠

**重要**：これは候補314の凍結SHA・正式152操作の合格台帳を置き換えない。現在は314起点の未コミットかつ複数担当変更を含む作業ツリーであり、最終候補のfreeze、統合CI、独立レビュー、元データとWindows nativeの原受入は未完。原152の現状 **33完了／43未達／76未確認** を本書では更新しない。合成GUIがPASSしても原期待値の全条件（実データ、失敗・復元、Windows、第三者レビューなど）を飛ばして正式完了へ昇格させない。

## 今回の実装と再使用・追加証拠

| 正式原ID／関係 | 観測済み機能・変更 | 保存した証拠／実際の限界 |
|---|---|---|
| H01 | 原`works.last_backup`降順最大60作者、folder/ZIP代表画像。`legacy_backup_history.py`、`viewer_backup_view.py`と原DBのみでshellのタブ有効化。元DBはread-only、ZIP抽出や原データ変更なし。 | `artifacts/p3-h01-legacy-backup-20261028/worker1-r8/impact.xml` 38 PASS。実旧DBのGUIとOS Explorer実操作は未受入。 |
| X02／X04 | 旧`file_ledger.status='unsure'`と管理保存近似の独立表示（混同禁止）。小中大88/144/200pxのサイズと選択状態。`failed_queue_readonly_db`の明示した凍結コピーのみ接続、実DB write/sidecarなし。 | worker-2 21 PASS＋合成Edge1280/1920それぞれ6チェックPASS。primeサービス接続＋無変更/元DB SHAを確認する回帰 `artifacts/p3-314-x02-wiring/r1/impact.xml` 35 PASS。物理削除X07/X08は仕様安全契約未受入のため実装しない。 |
| D09／D10 | 作品概要を開閉・Esc・本文頁送り。原HTMLを危険なDOMとせず安全なテキスト/限定HTTPSへ変換、頁数/境界、空/未取得/失敗、入力やIME、再開、判定/タブ離脱、resize。既存GET応答のdescriptionのみ投影し**追加Pixiv GETなし**。 | `artifacts/p3-d09-d10-summary-20261028/impact-r2/result.xml` 253 PASS、`.../gui-r9/result.json` Edge1280/1920で実操作PASS/source_changed=false。元APIレスポンスに本文が無ければ「未取得」とするため、実アカウントの全文機能は未完。 |
| L12 | 313合成容量テストの原条件と314作業ツリーを区別し、50→62作者の容量・0B・降順・任意再計測・故障時旧値保持・再起動cache復元を実画面で再検証。 | `artifacts/p3-capacity-auto-20261028/p3-314-full-l12-stable-r3/result.json` Edge1280/1920 × 初回/再起動4/4 PASS、`source_changed=false`、所有プロセス通常終了、port解放。Fletの全原UX、実旧DBの性能と自動復元は別。 |
| D15／#57 | D15は**振り返りLike行からのローカル保存**でありCSVエクスポートではない。verified receipt、queue done、保存済み判定、未保存フィルター、再起動復元を確認。閲覧履歴判定バッジ#57とは別原ID。 | `artifacts/p3-314-d15-review-r3/result.json` Edge4/4。実Cookieでの実保存、失敗・Retry・停止競合未受入。 |
| R05／R08 | おすすめ節を原既定順へ戻すCAS設定と、非表示理由genre/style/なし・Undo。 | 旧失敗/ソース競合を保持したうえで、未回収 `session57269` を再送せず回収。`artifacts/p3-314-recommend-expansion-r1/result.json` Edge4/4、source_changed=false、exit0・port解放。元UI全条件は別。 |
| L15／L16 | マイブックマーク選択、通常管理ジョブ一括受付（busy/operation冪等・部分失敗保持）、ジョブdone/verified receipt。 | `artifacts/p3-bookmark-bulk-20261028/r1/result.xml` 12 PASS、`.../browser-r3/result.json` Edge4/4。実Cookieは未受入。 |
| V07／V08 | 解像後の画像ズーム1〜4×と自然サイズclamp付きパン。Wheel/キーボード/drag/リセット/閉じる・ページ移動時復帰。 | `artifacts/p3-viewer-zoom-pan-20261028/worker2-tests-verified/impact.xml` 117 PASS、Edge2寸法各6項目。独立最終差分レビュー、native DPIは未受入。 |
| Vault P2 | `SocketFence` close失敗、登録GC、DNS/socket前拒否、`invalidate`のshutdown未確認sticky poison、`activate()`のpoison拒否、非OSErrorの失敗もfail-stop。保管庫のopen/availability偽状態防止、再解錠/auto/keeper禁止、UI再起動案内と制御無効化。`ApplicationServices.close()`は遮断未確認エラーを返しつつ他の所有資源を独立解放。 | RED `artifacts/p3-314-vault-revocation-failstop/r1/red.xml` 3 FAIL→`.../r3/impact.xml` 88 PASS。Worker-2 P2追加 `artifacts/p3-314-vault-poison-worker2/impact-final2/impact.xml` 86 PASS。最新E01統合回帰 `artifacts/p3-315-e01-vault-integration/r2/impact.xml` 135 PASS/0 FAIL、JUnit SHA `b9f88b9b796ca2569c69678fbfd2a0497542ba637e490d22b52e8954b93c5412`（含Windows実localhost TLS12）。実OS故障再現・独立監査は未完。 |
| E01 | 元Fletの設定入口から旧DBと全worksメタデータのみZIPへ。`legacy_metadata_export.py`新設とsettings/サービス接続。`pixiv_vault.db`＋`metadata.json`だけ、画像を含まないと明示、専用所有export root、SQLite整合/ZIP CRC/bytes/SHA、故障/取消/連続実行・アプリ終了競合保護、元DB SHAとmtime保全。 | `artifacts/p3-315-e01-metadata-export/r5/impact.xml` 32 PASS（旧段階）、最新は上記統合135 PASS。`artifacts/p3-315-e01-export-edge-r3/result.json` Edge1280/1920 × 初回/再起動4/4 PASS、各回2回保存で上書きなし、元ソースSHA不変、画像なし、通常exit0・port release、`source_changed=false`。実旧DB・配布版・長時間経過は未受入。 |
| U17 | 元Fletで開閉が無効だったダウンロード詳細バーを、保存されたクライアントごとの開閉状態・リアルjob snapshot・選択数で復旧。ダブルクリック・明示ボタン・ARIA、低いwindowの拒否理由、タブ往復維持、別クライアント分離。 | `artifacts/p3-315-u17-download-details/r1/red.xml` 未実装4 FAIL→`.../r2/impact.xml` 27 PASS、`.../edge-r1/result.json` Edge1280/1920初回/再起動4/4 PASS、GET追加0/外部request0/console0/exit0/port解放/ソース不変。原Fletと総合的なスペース比較・全native条件は未受入。 |

## 原要求・原票・安全契約の維持

- 正式152原IDは `docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md` が参照。上の各pytest PASS数は重複もある**限定ケース数**であり、152の分母へ足さない。
- 現作業ツリーの `HEAD` は元候補314の歴史的参照と別。315以降の最終manifest/SHAは、並列変更が静止したあとに改めて凍結する。過去のRED/失敗/`source_changed=true` 証拠は上書き・隠蔽しない。
- 合成fixture以外の元DB・画像・ZIP・Cookieを作成・移動・削除・改名・出力せず、実アカウントへのPOST/フォロー/ブクマ変更なし。原ファイルの物理ごみ箱/削除は今回禁止。外部公開・main統合・pushも未実施。
- 別枠ゲート：全採用機能／正式152操作実受入、実旧DB互換・保存復元、差分/レシート/故障/取消、総合UX・原Flet p50/p95/max、Windows native・DPI・配布別PC、CI exact-source・独立監査は未完。**Goal未達**。

## 再開／最終受入の手順

1. 独立workerの完了結果を回収し、担当境界と現物SHA/manifestを照合。既存証拠が有効なら再試験しない。
2. E01を含む最新生成物へのread-onlyレビューと必要な修正影響回帰を行う。workerのレビューがツール承認待ちなら未承認と明記する。
3. 元Flet原期待152の操作/始動条件/期待/実操作/観測/個別証跡/SHAを固定候補へ紐づけ、正式33/43/76から根拠がある行だけ更新。
4. 元ユーザーデータ保全・実Cookie GET承認範囲のユースケース、失敗/Retry/停止競合、UX/性能、Windows native/配布/別PCを原条件で受入。
5. 全要求・CI・独立監査・権限境界が証拠で成立するまではStage6 Goal宣言をしない。
