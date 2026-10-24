# Phase9 managed scheduler — 後任によるローカル最終受入（2026-10-24）

## 判断と安全境界

- 対象は `S:\Projects\PixivVault-NiceGUI-wt\scheduler-managed` / `phase9/scheduler-managed` の既存 dirty 候補。元の Flet PixivVault、原DB、画像、ZIP、Cookieには書き込まない。Codexは使用せず、開発操作は CoS Core 経由。
- 再開基点の HEAD は `4c1b29db400b2f02c79391dc93866166ea1d39c4`（文書だけの引継ぎ push）。それ以降の製品ソース・試験は、この記録作成まで未コミットだった。過去の Phase7/8 worktree には戻していない。
- この文書の `APPROVE` は **Phase9最終候補の独立した静的コードレビュー**。製品全体の移植完了・実Cookieデータ成功・他PC/全DPI/実サービス成功を意味しない。metadata backfill、Nana7mi Shaft/vault、うごイラ、小説ほか未移植の機能は別ゲート。

## 追加の独立レビューと解消

1. 既存の P2-1〜6 修正後の独立レビューは当初 `BLOCK/P1=0/P2=3`。お気に入り除外後に稼働中dispatchが孤児deferredを書き戻す競合、成功扱いだが進捗なしのbacklog高速反復、編集中フォームの CAS revision だけが更新される2-client上書きを修正した。
2. `defer_work` はledger内同一RLockで最新 favourite を再確認。旧台帳オープン時の孤児deferredを除外。runnerは保留件数が減らない場合も60秒の停止可能なwait。曖昧なlegacy receiptはdeferredから外し、無人の無限再試行を防ぐ。
3. SchedulerView は dirty フォームの入力値とCAS revisionを固定し、プログラムからの `set_value` 通知を抑止。`編集を破棄して再読込` ボタンで競合から安全に復旧できる。
4. 追加レビューで、未通知の `saved` と `unknown` の旧世代receiptを無人runnerが約5秒ごとに再ハッシュするP2を検出。`saved` は再照合から外す一方、永続通知を保持。`unknown` は軽量なheader/index確認を先行し、**受付済みoperationのexact fingerprint** と一致したときだけ全ページSHA/decodeを行う。後着の正しい世代は従来通り救済。
5. 例外後に実際は受理されていた `failed` dispatch item についても、operation IDからfingerprintを復旧して同じ軽量→完全照合に通す。曖昧な登録は受付証明なしで勝手にsavedにしない。履歴で発生したfailed事実は保持。
6. 本物の `submit_author_batch` によるlegacy UNKNOWN、旧台帳移行、無進捗backoff、interrupted barrier、deferred-only履歴、失敗集約維持、UI CAS/破棄、unattended unknownとfailed crash-windowの `ApplicationServices.reconcile_managed_scheduler` 実呼出しを回帰試験へ追加。

**独立レビュー**: CoS Coreから起動した読み取り専用 Claude Code が現行主要コード全体と差分、元P2修正を監査し、最終判断 `APPROVE / P1=0 / P2=0`。P3は破損receiptが全reconcileを停止するfail-closed、未処理作者のcancel遅延、履歴長期肥大、他クライアントの設定競合時の編集破棄など。コード・試験・実画面/CIの合格と混同しない。以前の古いworkerの限定承認は最終全体承認として使用していない。

## 同一製品ソースの最終検査

- **全pytest**: `artifacts/phase9-final-failed-recovery-20261024-full.xml`; **1213 passed / 1 skipped / 1 warning**。skipはWindows symlink権限、warningはStarlette TestClient/httpx deprecation。専用 `tests/test_managed_scheduler.py` は **32 passed**。
- **Edge**: `artifacts/phase9-failed-recovery-final-edge-20261024/result.json`; **success=true, 4/4**（1280×720/1920×1080 × 初回・再起動）。70個の合成dispatch itemが `saved`、deferred=0をchecker内のSQLiteから直接assert。console/page/foreign errors=0、normal exitとport releaseは4/4、製品ソース **59/59 SHA-256** がworktreeと一致。旧本番データは使用していない。
- **Windows native**: `S:/Projects/PixivVault-NiceGUI-wt/phase9-native-build-approved-final-20261024` に別スナップショットを作成、**117/117製品ソースSHA一致**でPyInstaller onedir/noconsole build成功。コピー先 `artifacts/phase9-approved-final-native-copy-20261024` とbuild distは **1050/1050 SHA一致、余剰/差異0**。コピーEXE SHA256 `C1A0F55630FB58590B8707A08F868CCFB44E9A7B4AD205508B657E47283A4BDC`。
- **Native実操作**: `artifacts/phase9-approved-final-native-acceptance-withpath-20261024.json`、**status=passed**。WebView/UI、画像decode、viewer、学習Undo、library、合成managed download、最小化/復帰、正常終了、所有PID 8件終了、port解放、強制cleanup0、process log errors0。run固有stemのスクリーンショット8枚。最初のcheckerは環境変数 `PYTHONPATH` 不足で製品起動前に失敗し、別名の新規実行で正常に通過した（失敗結果を合格に読み替えていない）。
- `git diff --check` 成功。必要な静的レビュー時の最新SHAは `managed_scheduler.py=C135CB7338DC8BEF1FC90F515A95E10A8929D908E1CF369F20090BF19BB00AF5`、`services.py=3330174D9678EC03BA3583CAB2DA9AA2D407EBB6631C4E90FA2D96E2100E21A1`、`scheduler_view.py=DF3DDEBE755FC2A541EA51BF3D80ED91DC6A1E07AD99F3E586057ABABC678B46`。

## 残るリリースゲートと保全

この文書を記した時点では `phase9/scheduler-managed` の製品コードcommit、push、PR、head SHAのWindows/Ubuntu/Chromium CI、mainへの条件付き統合・post-merge CIは未受入。全条件を現物で確認し、既存承認範囲で順次進める。PR/CI失敗時に無条件mergeしない。

`docs/PHASE9_EVIDENCE_INCIDENT_2026-10-24.md` に記載した共通 `webview-ui*.png` 上書き事故について、**事故前の元画像bytesは未復元**。今回のnative実行はrun固有名を使用し、既存共通名を再上書きしていない。所有者不明の古いpytest PIDは停止していない。実Cookie、元DB/画像/ZIPの変更、Shaft/vault通信は今回行っていない。

製品全体のFlet→NiceGUI移植完了を、このPhase9局所受入のGREENから推定しない。統合後は原版との差分棚卸しと残機能の独立実装・レビュー・同一候補受入を継続する。

## 追補: PR #30 初回Chromium CIのREDと厳密なチェッカー待機への是正

- 製品コード commit db997cfd13d3bf1354fbf7dac375dfb7fc6fdf13 をPR #30へpush。CI run 40051072467のUbuntu/Windows pytestはSUCCESSだが、Chromium作者差分DL検査がRED。mainは未統合で根本原因を調査した。
- GitHub Actionsの保存済み失敗証跡のindexは最終的に70作品、作品321の期待更新日、3つの世代receiptが揃っている。旧checkerは作品数70だけを確認して待機終了し、作品321の新世代CAS反映前に更新日のassertへ進んでいた。証跡は別ディレクトリへ保存し上書きしていない。
- 製品ソースは一切変更せず、tools/check_author_download_browser.pyだけで、全70作品・作品321の期待update_date・3世代receiptが揃うまで上限付きで再照合。index原子的置換時の一時的読取失敗も許容し、ledger側の全70 item saved・deferred 0を別途上限付きで厳密に確認する。SHA、画像pixel、receipt、完全GET、終了/port等の既存検査は緩めていない。
- tests/test_author_browser_race.pyに4件の完全合成・偽時計テストを追加し、件数先行・更新日遅延反映、破損JSONの復帰、永続未反映の失敗、画素異常を確認。Claude Sonnetはテストのみの独立読取検証でAPPROVE/P1=0/P2=0。Claude CLIの書込許可は非対話環境で拒否され、primeがCoS Coreからテストを作成した。
- 再検査: 全pytest 1217 passed/1 skipped/1 warning（artifacts/phase9-checker-final-full-20261024.xml）。Chromium合成初回/再起動 × 1280×720 / 1920×1080は4/4 SUCCESS、59製品ソースSHA一致、console/page/foreign errorsゼロ、正常終了/port解放4/4（artifacts/phase9-ci-checker-strict-poll-chromium-20261024/result.json）。
- 本追補時点では新PR headのCI、main merge、post-merge CIはまだ未確認。正確なPR head SHAに対しWindows/Ubuntu/Chromiumの各SUCCESSを確認してからのみ既存マージ承認を適用する。
