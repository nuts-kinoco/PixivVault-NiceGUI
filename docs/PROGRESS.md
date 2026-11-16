# 通常起動・初回設定の共有owner修正候補（2026-11-16）

この先頭節が本候補の最新状態。下段のB3/L14統合記録は比較元の履歴として保全しており、過去の「共有owner作成なし」「認証確認不能」を現在の仕様・結果へ戻さない。

## 準備済み・自動検証済み

- 開発rootのbranch `bootstrap/phase0-shell` / SHA `7125009928981a1247c0a5b67363ba0de1855a7e` と既存5件差分を保全。root checkoutへ製品コードを上書きせず、`artifacts/pv-startup-owner-fix-20261116/candidate` に差分候補を作成。git commit/push・依存変更なし。
- 比較元430件manifest SHA256 `a9fa5d142e9a89d43b871b28e757936e03663fc794b125bb0eb1d121aa59cd5f` を全件SHA/size照合。CoS原証拠 `S:/Projects/COS-PV430-REAL-RESTART-20261116-r1` は変更しない。
- 通常CLI/初回Nativeの正規起動で共有STOP ownerを作成・再読込する。専用schema、親anchor、provenance、DB/lock identityを保持し、owner欠損・成否不明・既存STOPでは再初期化しない。選択した旧mediaのSTOPは原本のbyteを保全し、GET中は旧writer lockも保持して共有ownerへ単調に反映する。履歴不明の構成は修復待ちとし、正式移行を新規利用の必須条件にしない。
- Nativeの結果をCookie読込不良、owner未成立、既存停止、ローカル構成不良、成否不明、実HTTP拒否、429、identity未確認、通信未完了に分離。CLIのCookie不良/owner構成不良もbounded診断とexit 2で案内する。秘密値・遠隔応答本文は診断へ出さない。Native起動時のローカルowner構成失敗も原因を回復画面へ保持し、一律の認証失敗・正式移行要求へ置き換えない。
- Cookie+保存先の設定保存→通常再起動→実register_pages/ApplicationServices/DemoShell→借用ONのパスワードdialog表示→キャンセルを生成Cookie・差替transportで確認。認証確認だけでは設定を保存しない。未設定のdisabledは正常であり、設定と再起動を具体的に案内する。
- コマンド: `.venv/Scripts/python.exe -u artifacts/pv-startup-owner-fix-20261116/evidence/run_validation.py --name new-sealed --suite new` → **24 PASS**。`--name first-local-final --suite old-first-local` → **10 PASS**。旧health試験1件の「ownerを作成しない」期待も正規作成/再読込の検証へ更新した。変更前430の同10件は `--name baseline-first-local --suite old-first-local --baseline` → **10 PASS**。新規pytestはCIから明示起動する。
- 関連回帰は同runnerの `--name regression-final --suite regression` → **131 PASS**。共有owner/transport、Native認証・保存先・cleanup、正式移行、B3/L14の18件を含む131件を対象とする。起動・回復画面の最終限定確認は `--name startup-confirmed --suite startup-focused` → **33 PASS**。原ログ/XMLは本候補の `../evidence`。実ネット接続はrunner監査で禁止し、asyncioのローカルsocketpairのみ許可。独立read-onlyレビューの重大2件（初回busyの再試行不能、429の表示）を修正し再確認済み。

## 受入済み証拠の再利用

B3/L14統合18 PASSは本候補でも関連回帰内で再検証。L14の広い既存証拠190 PASS / 1 FAIL / 1 SKIPは元の範囲で再利用し、合計件数へ加算しない。既存fail `test_selection_more_and_stable_existing_cards`、skip `test_foreign_root_and_links_rejected`、サムネイル404とwrapper exit 1の制限を保持する。元のevidence-reuse.jsonとCoSの実環境拒否・後片付け証拠を参照。

## 実機待ち

本担当はGUI操作・実Cookie読込・実Pixiv通信・借用認証成功・実画像・実bookmark_count・実ブクマON/OFFを実施していない。自動試験をその合格へ置き換えない。次は今回のCoSに本候補を渡し、実Cookie→Native設定保存/再起動→指定借用ON、独立した自己Cookieでの詳細/保存receipt/catalog/同作品カード/filter/実count、許可済み最小ブクマON/OFFと原状復帰を確認する。本人パスワード等の本人操作だけを依存待ちへ分ける。429/認証challenge/CAPTCHA/再ログイン要求時は実通信を停止して状態を保全する。旧owner・STOP原本を消したり、空ownerを手作りしたりしない。

実機用差分・候補の固定識別・条件別残件・一括引継ぎは `../../evidence/REPORT.md` と `../../evidence/COS_HANDOFF.md`。本候補の受入は自動検証済み/実機待ちであり、製品完成・標準配布・main統合の合格ではない。

---

# B3 + L14 統合隔離候補（2026-11-16）

## 現在の対象と準備済み成果

423件正本を起点に、採用済みB3修正版428件とL14 425件の差分を一つの隔離候補へ統合した。main統合・配布ではない。製品/試験の変更パスは重複なし。競合したPROGRESSはこのファイルで両方の全文履歴を残した。

- 起点manifest: `4a7ed933c260dcbdee27bae376a05bf4bcd646e7b80f68aa0a08c0040461ef5d`（423件）。394件の保存commitは全体の代用にしていない。
- B3採用元: `a0670117f8f3f760e5434c949b823e691e20e51032a5eb070fe63623b271aa2f`（428件）。自己/他処理の停止理由を含む4製品ファイルは採用元と同一byte。後続の局所Native 2ケースは元のBATCH_RESULT/BATCH_AUDITを再利用。独立レビューで局所閉鎖済みという最新の統括指定を優先し、下段の古い「未実施」を現状へ戻さない。
- L14採用元: `22fb209276d29b18a2a3835708a474c712ae1ddf2f4f149610ac7008343436f7`（425件）。11製品モジュールとnative検証器は採用元と同一byte。最終192ケースは190 PASS・既存FAIL 1・既存SKIP 1・ERROR 0で完了済み。再実行待ちにはしない。
- B3のpyproject/CIは補助設定として採用した。L14の試験用pytest.ini/root切替は製品差分に混ぜず証拠側に記録。依存変更・インストールなし。
- 新規の結合試験は実詳細DTO処理の合成429待機後の成功→新receipt→catalog→カード/既存filter、共有停止時に詳細/既存値/receiptを更新しない経路を、同じ候補importと同じ実UI Clientで検証する。

## 受入結果と独立判定の境界

今回の限定オフライン回帰のコマンド・件数・原ログ・条件別結果は [今回報告](../../evidence/REPORT.md) と [実行ログ](../../evidence/regression.log) を参照。過去の35/8/192件と今回の件数を足し合わせない。過去の既存FAIL/SKIPは消さず、元証拠と再利用対応を別記録する。

全登録ファイルのSHA/size固定は [manifest](../../evidence/manifest.json)、差分/由来は [統合記録](../../evidence/merge-provenance.json)。独立レビューと必要なCoS差分確認は [引継ぎ](../../evidence/REVIEW_HANDOFF.md)。統合候補の正式合格、標準製品全体や統合Nativeの合格は付与しない。

## 実機待ち・保持した制限

実機GUIはCoS担当。今回GUI操作・実通信・Cookie入力・実429・旧実DB/receipt書換えは実施しない。既存STOP/owner/401/403/送信/待機/GET保護、旧receipt互換を変更しない。ai_type/create_date/x_restrict等の新しい機能、数値ゲートを追加しない。元423/425/428、既存証拠、製品root差分、正式台帳、mainを変更せず、commit/push/公開もしない。

## 引継いだB3全文履歴

以下の履歴内の相対参照は原候補を基準とする。原文byteは今回証拠の `source-progress/b3/PROGRESS.md` と `source-progress/l14/PROGRESS.md` にそれぞれ保持し、SHA/sizeで照合する。原候補の局所合格や過去の未実施記述を、この統合候補の合格/未完へ無条件に読み替えない。

# B3-C2-UI-001 訂正候補（2026-11-16）

今回の最新記録はこの節。下段は固定427件候補から引き継いだ履歴であり、旧35 PASSや前回の文言修正をB3条件2の達成根拠にはしない。CoS訂正層は条件2を一部成立・表示問題ありと判定している。正式台帳とNative合否は変更していない。

## 今回の差分と合成確認済み

- 起点: 固定427件manifest SHA256 `b53457d0bb5e2103e6df62187c4ac3e29f9a34a34349f515f9ff3b20662080e7`。別隔離候補へ427/427一致でコピーし、旧候補・旧証拠は不変。
- 製品差分は `request_policy.py` の表示用 `stop_cause` と `ui/request_status_view.py` の主体別文言だけ。既存の自己上限/共有停止の分岐で表示メタデータを添え、送信・待機・回数・停止条件・owner・保護を変更していない。
- 他処理による停止は「別の取得処理が…」「この操作自身は試行上限に達していません」と対象行自体で説明。試行番号2/5や5/5は、この回の送信前に止まったことも明記する。自身が上限に達した場合は自身が原因と説明する。原因情報のない旧5/5記録は主体を推測しない。
- 必要な合成pytestは新規6件と関連既存2件、8 PASS / 0 FAIL / 0 ERROR / 0 SKIP（1.81秒）。最終の製品/試験byteで実行済み。起点では新規6件が期待どおりFAIL、既存2件はPASS（2.10秒）。
- 原Nativeの2/5+5/5記録の再表示、実ポリシーの対象1送信/2番目で停止と4送信/5番目で停止、自己上限1/1・5/5を検査。表示前後のSTOP/owner/intentファイル、ポリシー全状態、送信数は不変。起点との制御記録比較と、表示メタデータを除いたポリシー全体ASTの一致も証拠化する。
- 条件1/3とL14の既済試験は反復していない。実通信・実Cookie・実429・共有GUI操作なし。

## 準備済み・独立レビュー/CoS差分確認待ち

固定428件のSHAは今回の [封印結果](../../evidence/seal-result.json) と [manifest](../../evidence/manifest.json)。[差分](../../evidence/changes.patch)、[条件別結果と原ログ](../../evidence/REPORT.md)、[今回だけの確認事項](../../evidence/REVIEW_HANDOFF.md) を参照。独立判定、修正後Native受入は未実施。commit/main統合/pushなし。

実行: `S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B S:/Projects/PixivVault-NiceGUI/artifacts/pv-b3-c2-guidance-fix-20261116/evidence/run_checks.py --run-name regression`。実際のpytest引数とS専用環境は原ログへ記録。bytecode無効、タスク外Python書込み/実transport接続を拒否。

## 引継いだ旧B3記録（以下は履歴）

# B3 表示改善候補（2026-11-16）

この記録は隔離候補だけの作業記録。正式台帳・S13・製品全体の合否は変更していない。

## 合成回帰で確認済み

- 起点: `S:/Projects/PixivVault-NiceGUI-x09-summary-20261115/candidate` の全423ファイル。`evidence/review-r2/manifest.json` SHA256 `4a7ed933c260dcbdee27bae376a05bf4bcd646e7b80f68aa0a08c0040461ef5d` と423/423一致を確認してコピーした。394件の保存commitは起点の代用にしていない。
- 過去の永続429 STOPと今回の設定待機・再試行を区別。過去のSTOPが再起動でも残ること、今回の上限停止は同じポリシーを共有する取得の起動内停止であることを明記した。
- 不確かな送信中記録の退避と正式移行の目的を明記。いずれもSTOP解除ではなく、履歴・所有情報を保持して確認を依頼する案内を維持した。
- 変更した製品ファイルは `ui/shared_get_recovery_view.py`、`ui/stop_recovery_view.py`、`ui/request_status_view.py` の3つだけ。共有ポリシーの他処理による停止を「この操作が上限を使い切った」と誤認させる表示も修正した。
- 新規16件・既存関連19件、計35件 PASS / FAIL 0 / SKIP 0（11.04秒）。実NiceGUI Clientの要素・ラベル・回復ボタン状態、実STOP/所有ファイルの識別子とSHA、再試行ポリシーの保持を合成fixtureで検査。実サーバー・共有GUIは起動していない。
- 起点に追加検査3件を適用すると、旧STOPの説明、正式移行の説明、共有ポリシー停止の表記で3件とも期待どおり失敗（1.69秒）。`baseline-red-r5.log` と `regression-r2.log` を参照。
- 既存の明示確認後だけの送信中記録退避、401/403/429 STOP非解除、owner/不明intentの保護、取消、正式移行でのSTOP保持を関連回帰で確認した。既済の無関係な試験は実行していない。

## 準備済み・独立判定待ち

- 固定候補はこの `candidate`。全ファイルの固定SHAは [manifest](../../../pv-b3-recovery-guidance-20261116/evidence/manifest.json) と [封印結果](../../../pv-b3-recovery-guidance-20261116/evidence/seal-result.json) を参照。独立レビュー前のためcommitは作成していない。
- 独立レビューには [差分](../../../pv-b3-recovery-guidance-20261116/evidence/changes.patch)、[条件別結果・実行手順](../../../pv-b3-recovery-guidance-20261116/evidence/REPORT.md)、[最小引継ぎ](../../../pv-b3-recovery-guidance-20261116/evidence/REVIEW_HANDOFF.md) を渡す。自己検査を独立判定には数えない。
- CI設定には新しいpytestを明示起動する手順を追加した。CIの実行・push・main統合は行っていない。

## 実機未実施（今回の対象外）

実Pixiv GET/POST、実Cookie、実429、共有GUI、実機の見た目・性能は未検証。自動解除・履歴消去・自動再送、待機値や送信制御、401/403・owner保護の変更はない。

実行: `S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B S:/Projects/PixivVault-NiceGUI/artifacts/pv-b3-recovery-guidance-20261116/evidence/run_checks.py`（cwdは製品root）。ラッパーは実際のpytestを起動し、対象35件・XML・環境を記録する。`TEMP/TMP/TMPDIR`、ログとキャッシュ先は本タスクのS領域、bytecode無効。Windows asyncio内部ソケット対以外の接続と、隔離タスク外へのPython書込みを監査フックで拒否する。


## 引継いだL14全文履歴

# L14 bookmark_count 隔離候補（2026-11-16）

## 準備済み

- 正本423件 manifest `4a7ed933c260dcbdee27bae376a05bf4bcd646e7b80f68aa0a08c0040461ef5d` を起点に、既知bookmark_countのDTO・永続intent・新規receipt・catalog保持、legacy補完、作品カード表示を実装。
- managed既知値（0含む）優先、managed未知時のみlegacy既知値を保持。補完は名前空間付き作品IDで照合し、membership／downloaded条件は維持。既存bookmark_min実装は変更なし。
- `evidence/run_tests.py` からpytestを明示起動。合成データのみ、外部socket接続を遮断。Windows asyncio内部socketpairには通常実行環境が必要だったため正規承認を取得。TEMP／TMP／TMPDIR／pytest basetemp／cacheはS内、bytecode禁止。
- `evidence/regression.log` / XML: 192件中190合格・1既存skip・1既存失敗。新規L14回帰48件は全合格。旧件数文言を期待する失敗1件は正本の未変更コピーでも再現（`baseline-counter.log`）。既存試験は変更していない。
- 最終LibraryView整理後の限定再検証: `evidence/run_final_checks.ps1` → `final-ui.log` / XML、17件全合格。
- この候補はGit commitではない。`d320e0e3ad08962b73520ecbb791dc983c534bd3` は起点全体を表すSHAではない。固定内容は同親evidence内の `candidate-manifest.json` と `candidate-manifest.sha256` を参照。

## 受入済み

今回、Native受入・独立出口判定・正式L14の新規合格は付与していない。既存公式台帳は変更していない。

## 実機待ち・次の作業

独立担当が固定候補・条件別結果・差分をコードレビューし、許可された実機環境で既知1200／既知0／未知の作品カードとbookmark_min境界を確認する。元L14の広いL-1条件・既存の正式partial_unconfirmedは本機能だけで完了扱いにしない。

main変更・統合・push・共有GUI操作・実Pixiv通信・実Cookie使用・既存DB／receipt変更は実施していない。services、STOP処理、owner、送信保護は差分なし。
