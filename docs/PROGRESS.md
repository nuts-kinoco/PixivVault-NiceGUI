# 移植進捗

更新: 2026-10-22、引継ぎ後の継続開発。今回の作業単位: `phase3/legacy-baseline`。

## 最新の進捗照合とフェーズレビュー

公開済みPR #1は `7125009`、後続Draft PR #2は `3947c95`。それぞれWindows/Ubuntu/ChromiumのCI3ジョブ成功を現物で確認した。PR #2のbaseは `bootstrap/phase0-shell` のまま維持する。PR #2のbrowser初回はChromium配布先のTLS不一致で未実行となったが、失敗jobだけの再実行で成功した。製品コードやTLS検証を変更して通したものではない。
`3947c95` だけをarchiveしたソースからWindows onedir/noconsoleを再ビルドし、別フォルダーのexeでビューア・学習/Undo・最小化/復帰・通常終了・観測8プロセス終了・ポート解放を確認した。archive単体は131試験成功。175% DPIの実機受入であり、全DPI・別PCの受入ではない。`WINDOWS_PACKAGING.md`に正確なSHAと証拠を記録した。
実画面で画像が自然寸法のまま拡大されない不具合と、viewer操作に既定primary色が残る不具合を発見し、viewer専用CSSとcolor指定で修正。画像部品の世代/decode処理は変更していない。修正後は実Edgeの一覧/学習/画像競合の3scriptが成功、画像競合13項目成功。1280×720/1920×1080の画像表示と操作色を目視確認し、別担当の静的レビューでも重大な退行なし。詳細は `PHASE_REVIEW_2026-10-22.md`。

学習DBは別のDraft PR #3へ分離した。古いUndoが別接続の新しい判定を消す競合を、履歴更新triggerによる世代と`BEGIN IMMEDIATE`内の照合/取消で拒否する。同値・ABA・旧writer・失敗時不変・元集計互換に加え、世代読取直後の別writerがlockされる試験を確認。constructor以外115メソッドのAST一致を再確認し、UIは引き続きメモリ台帳を使う。operation IDの永続冪等性は未実装。

旧Fletと新版で、共通の合成PNG・1920×1080物理client・175% DPI・warmキャッシュ・600ms入力間隔・同じscreen取得方式による30遷移がそれぞれ完走した。観測p95はFlet432.402ms、新版227.204ms。ただしcapture中央値約195〜197msと表示画像面積・元shell・配布方式の差があるため、製品の速度改善率とは扱わない。private bytesのプロセス群合計はFlet376.25MiB、新版578.11MiB。詳細と失敗試行は `NATIVE_OBSERVATIONS_2026-10-22.md`。

**段階3全体は未完。** 原版viewerはrankingタブ内を覆うため、現在のviewport全体overlayとのヘッダー/ナビ・タブ切替互換が未受入。完全に条件を揃えた製品性能比較、cold、全DPI/別PC、残り6画面も未完。UIの131試験と、DB21・比較準備27を含む統合179試験を混同しない。統合試験は179 passed / 7.16秒。実機測定後は専用Flet4プロセスと新版8プロセスを通常終了した。
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

旧Fletと同じ画像・件数・画面条件での速度/メモリ比較、全画面の外観一致、実ライブラリ接続、実学習の保存、ダウンロード/停止/再開、トレイ/URI/全DPIは未完。
読取アダプタは一時fixture DBでの互換確認まで。任意の本番DBを暗黙に開く入口は作っていない。
旧Fletの限定ソースを合成fixtureで隔離起動する準備は別単位で進めている。現在のlauncherはランキングtabのみのため、原製品shell全体の受入や性能値には扱わない。

## 次の受入

原版同様のranking内容領域へviewerを収め、header/navを表示したままタブを離れた場合のclose・フォーカス・キー復帰を検証する。現在のshellにはclose呼出しがあるが、非表示カードへのfocus復元を避ける分岐と実操作受入が必要。
その表示条件を揃えた後、capture負荷を下げた計測方法を再校正し、cold/warm・順序・配布条件を分けて比較する。UI/DB/比較準備のDraft依存関係を保ち、既定UIの置換や本番書込み・マージには進まない。
