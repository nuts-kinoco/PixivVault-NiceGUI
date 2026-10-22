# 移植進捗

更新: 2026-10-23、レビュー後のマージ承認に基づく継続開発。最終ゴールは移植完了、各フェーズは途中のマイルストーン。現在の検証単位: `phase4/pixiv-auth-compat`（実Cookie読取の認証HTML・masked一覧互換と実画面受入）。

## 最新の進捗照合とフェーズレビュー

PR #20はhead `69ce42c6651605bf35fcb62c5dbfae24957529c5` のCI全成功後にmain `e8835c4e4209efe3eb76c296889c672b0e6a3898` へmerge。継続時にmain CI run39784532379も全成功を再照合した。

後続の認証互換は旧metaとNext.js本人情報を厳格に検証し、表示不可masked作品が一覧全体を失敗させる問題を分離した。製品時点677pytest、検証器の偽陽性・終了要求回帰を加え681pytest、実reader+合成transportの新旧HTML各2解像度Edge、同一コピーexeの合成旧DB経路・GDI復帰・通常終了が成功。実Cookieの専用Edgeでも検索/選択/サムネイル/2ページdecode/I・Tab/戻りfocus/タブ往復を確認した（GET102、うち画像52、書込0、Cookie原本不変、実データ証跡保存なし、通常終了・port解放）。過去2試行のAssertionErrorと合成終了待ち失敗は保持し、一般的な根因解消を主張しない。詳細は `PIXIV_AUTH_COMPAT_REVIEW_2026-10-23.md`。このbranchのhead/main CIは別ゲート。段階5のjobs-pipeline/queue-stateは別のdirty worktreeで並行しており、上書き・重複統合しない。

タグ条件PR #19は並行PR #18を取り込んだhead `8233586ff1982ef0726bb7a7aec787532c8f12d5` で全581pytest、CI run39779893130全3ジョブ成功後、`13ec540c2d3364c7cd0bcf4d9ad1d1c73c784bd8` へmerge。main CI run39780880773も全成功。実データ書込みやフェーズ全体の完了へ拡大しない。

後続ではWindows native初期配置を製品側で修正。プライマリ作業領域とDPIから論理座標を内向きに計算し、既存窓を移動せず初回生成へ反映する。対象70/全635pytestと独立レビュー、既存Edge4系統/旧DB/合成transportが成功。コピーexe `b49de6ff...` の2起動とも、配置補助なしで従来のclient3336×1826物理pxを維持し、初期origin251,144、DPI168、UI/GDI復帰/通常8プロセス終了/port解放が成功。製品43入力、配布1050ファイル、合成fixture3ファイルのSHA一致を確認。詳細は `NATIVE_STARTUP_LAYOUT_REVIEW_2026-10-22.md`。任意モニター・全DPI実機・別PCは未完。

旧SQLite/保存画像/マイブックマーク読取統合はPR #17へcommit `97efc035ca5096b8c135d69f86d49c93435ae5cd`としてpush。ブラウザCIの依存取得がAzure Ubuntuミラーで時間切れになったため、テストや閾値は変えず公式Ubuntuミラーへ切り替えるCI修正を独立レビューし、head `e57d83d2ab608f7902a78701f860f1150fc076ba` のCI run39776394742全3ジョブ成功後にmergeした。main `70922d9add8d0f1fee7affe69560209540c16b29` のCI run39777248515もWindows/Ubuntu/Chromium全成功。

次に原版のタグ含む/除外・AND/OR、280px押し出しパネル、NOWチップ解除を実装。作者代表と詳細全ページは絞らず、条件はDL済み/ブックマーク間のみ共有してclient間を分離。独立レビュー指摘の固定ボタン遅延イベントをブラウザ世代照合で修正した。製品凍結531pytest、検証器回帰を加えた最終545pytest、実Edgeの既存4系統・旧DB・合成transport取得失敗/復旧/2client試験が成功。同一コピーexeでタグ操作と最小化復帰・正常8process終了まで合格したが、初期画面外配置を避ける明示検証条件付きであり、自動初期配置の修正はWindows残件。詳細は`LIBRARY_TAG_FILTER_REVIEW_2026-10-22.md`。この変更のPR/head/main CIは別ゲート。

PR #14後のPR #15/#16統合とmain `48c718fe6f8702606496f44e5ed7a9d24cd72050` のCI成功を確認。旧DB/保存画像・マイブックマークUIを独立worktreeへ統合し、未DL作品viewer、検索/選択/戻り、4経路失敗復旧、旧cache破損隔離、novel/illust同番号分離、2clientの画像snapshot保持を実装した。製品コード凍結時516pytest、既存4経路・旧DB接続・合成APIを通す実Edge 1280/1920が成功。配布1050ファイルを別フォルダーへコピーしSHA一致を確認。同一コピーexeで旧SQLite・保存PNG・永続ブクマ検索/選択/viewer、最小化/復帰GDI、通常8プロセス終了とport解放が成功（175%開発機限定）。実Cookie原本の読取はツール安全性チェックに阻まれ未実行のため、ライブ認証/実API受入とは区別する。実装・独立レビュー・失敗試行・最終証拠は`READONLY_BOOKMARK_INTEGRATION_2026-10-22.md`。PR head/main CIは別ゲート。フェーズ4全体と更新系は未完。

PR #9で初期ライブラリ作者経路がmainへ先行統合されたため、追加修正のみを別branchへ整理した。作者51件以降の明示追加、詳細中の古い別作者クリック拒否、再訪できなくなる回帰修正、作者カード閲覧数表示、欠落したnative UI guardと検索selectorを補正。独立レビューの再現→修正後は全体358pytest成功。実Edge4系統・ソース前後hash一致・port解放を確認。実装同一SHA bea9cbdのWindows onedir/noconsoleを別フォルダーから実走し、library導線、最小化復帰のGDI画素、通常8プロセス終了とport解放成功。175%開発機だけの受入で、原版未移植範囲は引き続き未完。詳しくは`LIBRARY_AUTHOR_VIEW_REVIEW_2026-10-22.md`。

次の段階4の限定単位として、合成ライブラリの作者4件集約→作者01の60作品98ページ→共有viewer→戻るを移植した。検索前代表固定、検索は作者一覧のみ、50ページ追加とDOM/scroll保持、0/1始まり変換、未知ページ数、保存日/閲覧数ソート、カードサイズ同期・client分離・旧イベント排除を確認。別worktreeで統合353pytestが成功。実Edgeの1920×1080/1280×720と2 client、画像decode・合成503 retryも成功し、実ブラウザ4系統のランナーも全0で完走（同条件の証拠に限定）。この単位の記録は `LIBRARY_AUTHOR_VIEW_REVIEW_2026-10-22.md`。PR headのCI確認と統合は別ゲートで、段階4全体の合格ではない。

学習保存PR #6は、実装`1011127f4e86c2c3a34583b1aae2836b04655c21`の試験・配布受入と、文書だけを追加した最終head`f35bd54908f6504230550c4373e19a3676eb0734`のCI run39717369777全3ジョブ成功後、`bf7d00637cfbd3257c2c31b2c476cc7e17a17735`としてmainへmergeした。main CI run39717642107もWindows/Ubuntu/Chromiumの全3ジョブ成功。

主画像比較PR #7は、独立レビューと最終head`c9ecb85198f158c3b51a96fe3976cbaac2988581`のCI run39722305638全3ジョブ成功後、`1261d020be52b76007e3f0da8a6c4f769fad98b5`としてmainへmerge。main CI run39724415694もWindows/Ubuntu/Chromiumの全3ジョブ成功。

続くコード`76ddc0fc2cadf36b635a4c3e02f649fe197c10a6`で、native最小化/復帰後の子geometry・DPI・全client実GDI画像を必須検査にした。初回に一時reparentを検出し、同identityが約51ms後の観測で元に戻ることを別診断で確認。所有権を維持した期限内再検査と標本破棄を追加した。対象112pytest、全体329pytestが成功。製品コードが同一の1011127配布exeへ標準checkerを適用し、学習UI操作あり/通常一覧の2回で実画素・通常8プロセス終了・ポート解放が成功した。primeと独立担当が4全画面を確認し、最終レビューに重大残件なし。新buildや製品復帰処理の変更ではなく、175% DPIでの追加受入。詳しくは`NATIVE_RESTORE_REVIEW_2026-10-22.md`。

先行の主画像比較では、旧Flet隔離launcherへ任意のlogical Page paddingを追加し、原UI7ファイルを保ったまま主画像の実描画矩形を一致させた。コード`6f2b66e53b275f0c614211e05d5f864e55c2f412`は独立レビュー済み、対象pytest22件成功。1920×1080物理client・175% DPI・同一PNG・868×579物理pxの画像で、Flet→NiceGUI→NiceGUI→Fletの30遷移ずつ、計120遷移が成功。60標本ずつの観測p95は250.330 / 122.386 ms（取得・照合負荷込み、51.1%短縮）。private bytes合計は408.199 / 539.828 MiB（32.2%増）であり、メモリ削減は確認できない。全画面校正、raw標本再計算、独立レビュー、通常終了と元source不変を確認した。詳細は`MATCHED_VIEWER_BENCHMARK_2026-10-22.md`。

開始前の最小化拒否/一様frame拒否2件は成功標本と別保存した。NiceGUIは外部ShowWindow復帰後に子window座標不正が残り、標準の最小化/復帰1cycleで実画素まで復旧した。この先行事象の原因は未確定。後続のnative gate強化は上記のとおり実施し、別試行の短いreparentと画面外配置の恒常化を同じ原因とは断定しない。今回の限定比較は原shell全体・cold・連打・配布exe・全DPIの性能受入ではない。

PR #5のhead `d5ba18a246da27c14071793413dbb45fe8a40e62` はレビュー/CI/測定器受入後、`bf1b04bb10881ac65d515376e230680a839f36a3` としてmainへmerge済み。head CI run39708638252、main CI run39709086223のWindows/Ubuntu/Chromium全3ジョブ成功を再確認して次の作業を開始した。

合成デモの学習UIを、明示opt-inの専用DBへ接続した。原版の直前1手Undoへ合わせ、保存成功まで画像/判定位置を保つ。全DB I/Oは専用workerで行い、画面close後も受理済み書込みを保存する。レビューでは受付直後shutdownの保存欠落、保存状態の読取失敗後に遅い画像通知が復旧案内を消す問題、DB本体の差替えを識別前に開く問題を修正した。既定はメモリ台帳、本番データへの接続はない。

全pytest266件、実Edgeの永続化10受入が成功。実DBロック中の別タブ検索、失敗再試行、再起動での既判定除外、2画面の古いUndo拒否、ページclose後の保存まで確認。元Database115メソッドのAST一致と元ソースSHAも維持。独立再レビューにblocking指摘なし。実装 `1011127f4e86c2c3a34583b1aae2836b04655c21` をPR #6へpushし、同SHA CI run39716131704の全3ジョブ成功。詳しい証拠は`LEARNING_PERSISTENCE_REVIEW_2026-10-22.md`。

同SHAのarchive266件が成功し、Windows onedir/noconsoleを再ビルド。別フォルダーのexeを同じ専用DBで2回起動し、既判定0→1→正常終了→再起動1→2と、reload後の次作品/Undoリセットを確認した。両回ともWM_CLOSEによる観測8プロセス終了、ポート解放、強制cleanupなし、予期しない各種error 0。DPI175%の開発機受入であり、他DPI/別PCは未実施。

ユーザーの追加要望による主画像の定量比較は、上記の同じ物理client/DPI・実画像paint矩形のwarmページ送りまで進んだ。先行の表示面積差がある数値は今回の改善率へ混ぜず、初回表示/coldは次の条件として残す。
さらに既存Cookieを用いるpixiv実APIの読取・実測が承認された。専用GET probeは原Cookieを変更せず、認証値・個人データ・実画像を出力しない。メモリ上のCookie parseでCRLFのCRが残る不具合を合成回帰で再現し、通常text fileと同じ改行処理へ修正、12 offline検査と独立レビューを通した。修正後homeはHTTP200（TTFB1231.299 ms / 全本文取得1271.653 ms）だったが、ログイン済みを確認できず停止した。追加の固定bool診断と14 offline検査で、保存認証Cookieは記録上期限切れ、事前prepared requestに認証Cookieがないことを確認した。元Cookieの前後比較は全試行一致。ランキング/作品/画像には進んでおらず、実API画像取得や実データUI接続の成功とは扱わない。次の実API条件はローカルCookieの更新。詳細は`REAL_API_MEASUREMENT_2026-10-22.md`。

ユーザーの「コードレビュー後、問題なければマージ」に基づき、実装担当と別の3担当が基盤・ビューア・DB/計測をレビューした。基盤PR #1は `7125009` を `bdefa877617fc0dc2632e8de5801f197f0caa41f` としてmainへmergeし、そのmain CI3ジョブも成功。PR #2は原版と異なるviewer境界とfocusを修正して `43d0c64bd7acb6f96f387f9ff3639478417c278f` へ更新し、同SHAのCI3ジョブとnative受入後、`8df86671bbb0d7c4639e8dbb07687cb792f39566` としてmainへmergeした。全製品の受入完了を意味するmergeではない。
`43d0c64` だけをarchiveし、専用srcのimport先を確認してWindows onedir/noconsoleを再ビルド。別フォルダーのexeで10:29 JSTに、viewerのタブ内境界・通常closeの元カードfocus・学習への離脱・キー判定/Undo・ランキング復帰focus・最小化/復帰・通常終了を確認した。観測8プロセス終了、ポート解放、強制cleanupと予期しないconsole/page/process-log errorなし。archiveは132試験成功。175% DPI限定であり全DPI・別PCの受入ではない。
実Edgeの一覧/学習/画像競合3scriptが成功。画像/操作競合は16項目。1280×720/1920×1080でviewerとタブ内領域の一致、header/navの可視と操作、画像contain、詳細による画像領域の不変、検索/100件/小カード/DOM/scrollのタブ往復保持を確認し、全画面PNGを目視した。通常closeとタブ離脱を分け、古いrAFが新しいfocusを奪わないことも検証。独立再レビューに重大残件なし。詳細は `VIEWER_BOUNDARY_REVIEW_2026-10-22.md`。

学習DBはPR #3へ分離。古いUndoが別接続の新しい判定を消す競合を、履歴更新triggerによる世代と`BEGIN IMMEDIATE`内の照合/取消で拒否する。同値・ABA・旧writer・失敗時不変・元集計互換に加え、世代読取直後の別writerがlockされる試験を確認。constructor以外115メソッドのAST一致を再確認。UI修正を取り込んだ `22519a8ba388dc3ef60dc03dabf7a375395af2d5` のDB固有diffは、独立レビュー済み `7455ac3` とバイト単位で一致した。同headのCI3ジョブ（run 39706179497）と統合先main `8df8667` のCI3ジョブ（run 39706266732）成功後、10:41 JSTに `77d9761d7f7a73ce2e61fe83aeca1b740d921537` としてmainへmergeした。UIは引き続きメモリ台帳、operation IDの永続冪等性は未実装。

旧FletとUI `3947c95` の新版で、共通の合成PNG・1920×1080物理client・175% DPI・warmキャッシュ・600ms入力間隔・同じscreen取得方式による30遷移がそれぞれ完走した。観測p95はFlet432.402ms、新版227.204ms。ただしcapture中央値約195〜197msと表示画像面積・元shell・配布方式の差があるため、製品の速度改善率とは扱わない。private bytesのプロセス群合計はFlet376.25MiB、新版578.11MiB。詳細と失敗試行は `NATIVE_OBSERVATIONS_2026-10-22.md`。

PR #4はレビュー済みコードと受入記録を `95e4fbc636419d78ffbebc94a6fa575aea53f171` に統合し、同head CI3ジョブ（run 39707235390）とPR #3 merge後main CI3ジョブ（run 39707106962）成功後、10:46 JSTに `36040ee73c13fc22e5686ff795e617da270e9a32` としてmainへmergeした。そのmain CI3ジョブ（run 39707677025）も成功。

次の独立単位では任意の `screen-gdi` 取得方式を追加。同じUI43d0c64の静止画を順序交替で20組比較し、全RGB画素が一致した。最終実装の検査込み取得p50はPillow220.079ms / GDI115.816ms、コピーだけは130.109ms / 26.959ms。校正3全画面の目視後、同nativeで30/30ページ遷移を完走（観測p50 115.605ms / p95 123.451ms、capture overhead p50 92.520ms）。測定器の改善であり、旧新版アプリの速度比較には換算しない。
GDI復元失敗0/-1/Noneの回帰と資源回収を修正し、独立担当が修正前RED→修正後限定47件成功を確認。統合210件 / 7.40秒も成功。GDI object計数0→1→0の自己確認、取得前後0→0、専用8プロセスの通常終了と両ポート解放、元ソースhash一致を確認した。詳細は `GDI_CAPTURE_REVIEW_2026-10-22.md`。

**段階3全体は未完。** viewerのタブ内境界・離脱・focusは今回受入済みだが、原shell全体と画像面積/配布条件まで揃えた性能比較、cold、全DPI/別PC、残り6画面は未完。UI132試験と、DB21・比較準備27を含むPR #4時点の統合180試験を混同しない。PR #4時点は180 passed / 7.10秒、GDI追加後の210件は上記の別実行。フェーズ判定は `PHASE_GATES_2026-10-22.md` に集約し、未実施を合格へ変えない。先行の30遷移測定後の専用Flet4プロセスと新版8プロセス、今回のnative検証8プロセスはいずれも通常終了した。
以下は公開済み基盤の過去記録であり、最新版のnative配布・全面移植の完了を示すものではない。

## 状態

公開pushと新アプリの実画面操作は承認済み。初回 `500902a` と、画像配信・学習状態・読取アダプタの `160dbbe` を公開済み。
この後続単位では、合成データによる一覧/ビューア/学習の実ブラウザ操作と、Windowsの最小配布物を検証した。
元のPixivVaultの作業ツリー・DB・画像・Cookie・環境は変更していない。UIは依然として架空120作品のみで動く。

**全面移植・外観一致・高速化の受入は未完。** ランキングと学習の代表経路は動くが、他6画面、実データ書込み、元shellと配布条件まで揃えた性能比較は未実施。

## 担当

- worker: 画像/資源寿命、学習、読取互換、ブラウザ検証、Windows終了監視の限定実装と独立レビュー。1ファイルの編集を担当範囲に分ける。
- prime: 接続・画像イベントの切分け、ブラウザ/WebViewの実操作、ビルド、統合確認、記録、Git。
- 上限8workerの承認はあるが、この再検証では通常2件並列で分担した。利用枠の消費量は未計測。

## 検証

環境: Windows / Python 3.13.11 / NiceGUI 3.18.0 / pytest 8.4.2 / Playwright 1.64.0 / Edge・WebView2 155.0.4283.62。
新repoの専用venvを使用。バージョン一覧は `requirements-lock.windows.txt`。全間接依存の固定一覧を保存したが、別PCでの再構築は未検証。

| コマンド | 実測結果 |
|---|---|
| `python -X utf8 -m pytest -q --junitxml=artifacts/final-pytest.xml` | 2026-10-22 02:55頃、122 passed、4.25秒、exit 0 |
| `python -m pip check` | No broken requirements found、exit 0 |
| `python -m compileall -q src tests tools` | exit 0 |
| `python tools/check_browser_suite.py --channel msedge --output artifacts/browser-suite-final` | 02:51–02:52 JST、一覧2サイズ・学習13項目×2・画像故障/遅着7項目・2ページ独立性を通過、予期しないconsole/page error 0 |
| `python tools/run_browser_checks.py --channel msedge --output artifacts/browser-integrated-final` | 一覧/学習/画像競合の3scriptが全てexit 0、試験用サーバーのポート解放を確認 |
| PyInstaller `--onedir --noconsole` の専用出力ビルド | exit 0。独自画像コンポーネントのJS同梱・元ソースとの一致を確認 |
| 配布exe `--check --check-output <絶対JSONパス>` | demo / synthetic / native requested・available true、exit 0 |
| `python tools/check_native_window.py --exe artifacts/restart-standalone/native-app/PixivVault-NiceGUI.exe --inspect-ui --output artifacts/restart-standalone/native-acceptance.json` | 02:55 JST、別フォルダーへコピーしたexeの表示・画像・ビューア・学習/Undo・最小化/復元・通常終了・子プロセス/ポート解放を通過、強制cleanupなし |
| `git diff --check` | exit 0 |

03:15 JST追補: 選択中のカードサイズにもQuasarの既定文字色が残り、青背景に青文字になる表示を実exeの画像で発見した。サイズ・学習判定・Undoの色はテーマだけで制御するように修正。
`python -X utf8 -m pytest -q tests --ignore=tests/test_legacy_baseline.py --junitxml=artifacts/final-ui-pytest.xml` は **124 passed / 4.31秒 / exit 0**。
除外した1ファイルは別作業中の未コミット旧Flet試験であり、このPRの124件には含まれない。
同じ修正後に `python tools/run_browser_checks.py --channel msedge --output artifacts/final-ui-browser` を実行し、一覧・学習・画像故障/遅着の3scriptがexit 0。
サイズ3段階の選択中文字と学習4判定の文字が、実ブラウザで既存の `text-main` 色になることを追加検証した。

ブラウザの比較条件は1280×720と1920×1080。50サムネイル、50→100件で既存DOM保持、カード大中小、検索/clear、作品内のページ送り、詳細とEsc二段、一覧へ戻るscrollTop保持を確認した。
学習は4判定、Undo、古い世代の拒否、通常ショートカット、repeat/IME/入力/ダイアログ/非activeでの抑止を確認した。
故障注入した画像のHTTP503/404だけを期待エラーとして記録し、他のconsole/page errorは失敗にする。
試験前後の製品ソースハッシュは一致している。生の報告・画像はignoredの `artifacts/` に保存し、gitには同梱しない。
122件の実行時間やPlaywrightの待機時間は、アプリのp95性能値ではない。

native実測のWebViewクライアント領域は1907×1044、`devicePixelRatio=1.75`。これを100/125/150%の全条件合格へ読み替えない。
本番ジョブ・トレイ・URI・別PCへのインストール・長時間運用は今回の最小配布試験に含まない。

実証して修正した箇所:
- Undo時、キャッシュ画像の `currentSrc` が以前のfragmentを返し、画像readyが回復しない不具合。要求ごとの実DOMノードとdecode完了、HTTP資源の一致を照合し、捕捉した要求URLを通知する。サーバー側の世代照合は緩めていない。
- Quasarの既定文字色と選択中タブ背景が共に青になる不具合。ナビゲーションの既定colorを外して既存トークンで制御し、computed styleとスクリーンショットで読める状態を確認した。
- 並行closeによる資源の二重解放。closeを排他し、並行実行試験で一度だけ解放されることを確認した。
- native終了監視で、プロセスの照会失敗を終了済みと誤認する経路。PID/作成時刻・列挙失敗・待機戻り値を確認し、不明を監視失敗として出す。故障注入試験と実exeの終了確認を行った。
- 並行統合で重複したCIジョブキー。ブラウザジョブを1つに統合し、重複を検知する回帰試験を追加した。

GitHub ActionsではUbuntu/Windowsのpytestと、専用Chromiumによる実ブラウザ試験を別ジョブで実行する。
`ab00367` のWindows/UbuntuのpytestとUbuntu Chromiumの操作試験は全て成功（run `39649991323`）。その後の文字色追補の最新CI結果はPR #1のChecksで確認する。古いコミットの成功で代用しない。

## 製品受入の未完

原shellと配布物まで揃えた速度/メモリ比較、全画面の外観一致、実ライブラリ接続、実データ学習の保存、ダウンロード/停止/再開、トレイ/URI/全DPIは未完。合成のwarm主画像比較と学習sandboxの保存・再起動は上記の受入範囲に限る。
読取アダプタは一時fixture DBでの互換確認まで。任意の本番DBを暗黙に開く入口は作っていない。
旧Fletの限定ソースを合成fixtureで隔離起動する準備は別単位で進めている。旧Flet隔離launcherはランキングtabのみ。NiceGUIの合成ライブラリは作者詳細まで追加したが、原製品shell全体の受入や性能値には扱わない。

## 次の受入

次は他DPI・最大化/長時間復帰の実画素検査、初回表示/cold、連続入力、原shellと配布条件まで揃えた比較を進める。主画像比較には約91〜96msの取得負荷中央値があり、短い応答の観測分解能には限界がある。100/125/150%と別PC、残り6画面も独立した必須ゲートとして残す。
マージは独立レビュー・該当headのCI・変更に必要な実画面/実機受入後に行い、先行PRのmerge後は後続のbaseと統合treeを照合する。実APIの追加承認は専用の読取計測に適用し、製品の本番DB接続や既定UI置換、フェーズの未達ゲート省略へ広げない。
## マイブックマークの合成UI（2026-10-22）

段階4のマイブックマークは、原版との照合・純状態モデルPR #12の後、合成110件/ローカルDL済40件の切替可能なサブビューへ進んだ。作者一覧状態と独立した選択・明示50件追加・合成再読込と取得失敗時のキャッシュ維持・共有ビューアのページ移動とタブ往復を扱う。UIレビューでbadgeの配置/古い操作世代/同じIDでのbadge差分/旧Moreを修正し、初回実画面のサムネ高さ0を描画構造で修正した。375pytestと2画面サイズの実Edge4系統が成功。詳細は `BOOKMARK_UI_REVIEW_2026-10-22.md`。本番ブックマークのON/OFF・実API DTO/画像配信・ダウンロードは未接続。
